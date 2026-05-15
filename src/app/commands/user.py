"""`blink user` — manage dashboard users and roles."""

from __future__ import annotations

from pathlib import Path

import typer

from app.auth.config import AuthConfig
from app.auth.env_loader import load_env_file, session_secret_fingerprint
from app.auth.mailer import smtp_configured
from app.auth.passwords import hash_password
from app.auth.repository import AuthRepository
from app.config.jobs_root import JobsRootOption, resolve_jobs_root
from app.server.auth_routes import issue_password_token, maybe_send_setup_email
from app.server.global_auth_db import connect_server_db, server_db_path

user_app = typer.Typer(help="Manage Blink dashboard users (global server DB).")

_JOB_ROLES = ("watcher", "solver", "job_admin")


@user_app.callback()
def user_group_options(
    env_file: Path | None = typer.Option(
        None,
        "--env-file",
        envvar="BLINK_ENV_FILE",
        help="Load variables from a file (e.g. systemd EnvironmentFile). Overrides your shell env.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Shared options for all ``blink user`` subcommands."""
    if env_file is not None:
        load_env_file(env_file.resolve(), override=True)


def _repo_for(jobs_root: Path) -> AuthRepository:
    conn = connect_server_db(jobs_root)
    return AuthRepository(conn)


@user_app.command("check")
def user_check(
    jobs_root: JobsRootOption = None,
) -> None:
    """Show auth DB path and session-secret fingerprint (compare with serve's environment)."""
    root = resolve_jobs_root(jobs_root)
    cfg = AuthConfig.from_env()
    db_path = server_db_path(root)
    typer.echo(f"jobs_root:           {root}")
    typer.echo(f"server.sqlite:       {db_path} ({'exists' if db_path.is_file() else 'missing'})")
    typer.echo(f"session fingerprint: {session_secret_fingerprint(cfg.session_secret)}")
    typer.echo(f"public_base_url:     {cfg.public_base_url}")
    typer.echo(f"route_base_path:     {cfg.route_base_path or '(none)'}")
    if not cfg.session_secret:
        typer.secho("BLINK_SESSION_SECRET is not set — tokens from this shell will not match serve.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if not db_path.is_file():
        typer.secho("No server.sqlite yet — create a user first or fix --jobs-root.", fg=typer.colors.YELLOW)
    conn = connect_server_db(root)
    try:
        pending = conn.execute(
            """
            SELECT COUNT(*) AS n FROM auth_tokens
            WHERE used_at IS NULL AND expires_at > datetime('now')
            """
        ).fetchone()
        n = int(pending["n"]) if pending else 0
        typer.echo(f"pending setup/reset tokens: {n}")
    finally:
        conn.close()
    typer.echo(
        "Tip: set BLINK_JOBS_ROOT and BLINK_SESSION_SECRET in /etc/blink/blink-serve.env (Option D: source that file); "
        "fingerprints must match serve or setup links show 'Invalid or expired link'."
    )


@user_app.command("list")
def user_list(
    jobs_root: JobsRootOption = None,
) -> None:
    root = resolve_jobs_root(jobs_root)
    conn = connect_server_db(root)
    try:
        repo = AuthRepository(conn)
        users = repo.list_users()
        if not users:
            typer.echo("No users.")
            return
        for u in users:
            flags = []
            if u.is_global_admin:
                flags.append("global_admin")
            if u.disabled:
                flags.append("disabled")
            if u.google_sub:
                flags.append("google")
            if u.password_hash:
                flags.append("password")
            if u.slack_user_id:
                flags.append(f"slack={u.slack_user_id}")
            extra = f" [{', '.join(flags)}]" if flags else ""
            roles = repo.list_job_roles(u.id)
            role_s = ", ".join(f"{jid}:{r}" for jid, r in sorted(roles.items()))
            typer.echo(f"{u.id}\t{u.email}{extra}")
            if role_s:
                typer.echo(f"  roles: {role_s}")
    finally:
        conn.close()


@user_app.command("add")
def user_add(
    email: str = typer.Argument(..., help="User email (login username)."),
    jobs_root: JobsRootOption = None,
    global_admin: bool = typer.Option(False, "--global-admin", help="Grant global admin."),
) -> None:
    root = resolve_jobs_root(jobs_root)
    cfg = AuthConfig.from_env()
    if not cfg.session_secret:
        typer.secho("BLINK_SESSION_SECRET must be set.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    conn = connect_server_db(root)
    try:
        repo = AuthRepository(conn)
        if repo.get_user_by_email(email):
            typer.secho(f"User already exists: {email}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        uid = repo.create_user(email=email, is_global_admin=global_admin)
        raw, link = issue_password_token(
            repo,
            user_id=uid,
            purpose="password_setup",
            session_secret=cfg.session_secret,
            public_url=cfg.public_base_url,
            route_base_path=cfg.route_base_path,
        )
        sent = maybe_send_setup_email(cfg, to_email=email.strip().lower(), link=link)
        typer.secho(f"Created user {email} (id={uid}).", fg=typer.colors.GREEN)
        if sent:
            typer.echo("Setup link emailed.")
        else:
            typer.echo("One-time setup token (store securely):")
            typer.echo(raw)
            typer.echo(f"Setup URL: {link}")
    finally:
        conn.close()


@user_app.command("delete")
def user_delete(
    email: str = typer.Argument(...),
    jobs_root: JobsRootOption = None,
) -> None:
    root = resolve_jobs_root(jobs_root)
    conn = connect_server_db(root)
    try:
        repo = AuthRepository(conn)
        if not repo.delete_user_by_email(email):
            typer.secho("User not found.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        typer.secho(f"Deleted {email}.", fg=typer.colors.GREEN)
    finally:
        conn.close()


@user_app.command("set-password")
def user_set_password(
    email: str = typer.Argument(...),
    password: str = typer.Option(..., "--password", prompt=True, hide_input=True, confirmation_prompt=True),
    jobs_root: JobsRootOption = None,
) -> None:
    root = resolve_jobs_root(jobs_root)
    conn = connect_server_db(root)
    try:
        repo = AuthRepository(conn)
        user = repo.get_user_by_email(email)
        if user is None:
            typer.secho("User not found.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        repo.set_password_hash(user.id, hash_password(password))
        typer.secho("Password updated.", fg=typer.colors.GREEN)
    finally:
        conn.close()


@user_app.command("reset-token")
def user_reset_token(
    email: str = typer.Argument(...),
    jobs_root: JobsRootOption = None,
) -> None:
    root = resolve_jobs_root(jobs_root)
    cfg = AuthConfig.from_env()
    if not cfg.session_secret:
        typer.secho("BLINK_SESSION_SECRET must be set.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    conn = connect_server_db(root)
    try:
        repo = AuthRepository(conn)
        user = repo.get_user_by_email(email)
        if user is None:
            typer.secho("User not found.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        raw, link = issue_password_token(
            repo,
            user_id=user.id,
            purpose="password_reset",
            session_secret=cfg.session_secret,
            public_url=cfg.public_base_url,
            route_base_path=cfg.route_base_path,
        )
        if smtp_configured(cfg):
            from app.auth.mailer import send_email

            send_email(
                cfg,
                to_addrs=[user.email],
                subject="Blink — reset your password",
                body_text=f"Reset link (expires in 72 hours):\n\n{link}\n",
            )
            typer.echo("Reset link emailed.")
        else:
            typer.echo("One-time reset token:")
            typer.echo(raw)
            typer.echo(f"URL: {link}")
    finally:
        conn.close()


@user_app.command("set-global-admin")
def user_set_global_admin(
    email: str = typer.Argument(...),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
    jobs_root: JobsRootOption = None,
) -> None:
    root = resolve_jobs_root(jobs_root)
    conn = connect_server_db(root)
    try:
        repo = AuthRepository(conn)
        user = repo.get_user_by_email(email)
        if user is None:
            typer.secho("User not found.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        repo.set_global_admin(user.id, is_admin=enabled)
        typer.secho(f"global_admin={enabled} for {email}", fg=typer.colors.GREEN)
    finally:
        conn.close()


@user_app.command("set-job-role")
def user_set_job_role(
    email: str = typer.Argument(...),
    job_id: str = typer.Argument(..., help="Job id (matches meta.job_id)."),
    role: str = typer.Argument(..., help="watcher | solver | job_admin"),
    jobs_root: JobsRootOption = None,
) -> None:
    if role not in _JOB_ROLES:
        typer.secho(f"role must be one of: {', '.join(_JOB_ROLES)}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    root = resolve_jobs_root(jobs_root)
    conn = connect_server_db(root)
    try:
        repo = AuthRepository(conn)
        user = repo.get_user_by_email(email)
        if user is None:
            typer.secho("User not found.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        repo.set_job_role(user.id, job_id, role)  # type: ignore[arg-type]
        typer.secho(f"Set {email} -> {job_id} role {role}", fg=typer.colors.GREEN)
    finally:
        conn.close()


@user_app.command("clear-job-role")
def user_clear_job_role(
    email: str = typer.Argument(...),
    job_id: str = typer.Argument(...),
    jobs_root: JobsRootOption = None,
) -> None:
    root = resolve_jobs_root(jobs_root)
    conn = connect_server_db(root)
    try:
        repo = AuthRepository(conn)
        user = repo.get_user_by_email(email)
        if user is None:
            typer.secho("User not found.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        repo.clear_job_role(user.id, job_id)
        typer.secho(f"Cleared job role for {email} on {job_id}", fg=typer.colors.GREEN)
    finally:
        conn.close()


@user_app.command("link-slack")
def user_link_slack(
    email: str = typer.Argument(...),
    slack_user_id: str = typer.Argument(..., help="Slack member id (e.g. U012AB3CD)."),
    jobs_root: JobsRootOption = None,
) -> None:
    root = resolve_jobs_root(jobs_root)
    conn = connect_server_db(root)
    try:
        repo = AuthRepository(conn)
        user = repo.get_user_by_email(email)
        if user is None:
            typer.secho("User not found.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        repo.set_slack_user_id(user.id, slack_user_id.strip())
        typer.secho(f"Linked {email} -> slack {slack_user_id}", fg=typer.colors.GREEN)
    finally:
        conn.close()

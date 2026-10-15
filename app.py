import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import click
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import case, or_, text
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Sign in to continue."
login_manager.login_message_category = "info"

ROLES = ("administrator", "support agent", "requester")
STATUSES = ("Open", "In Progress", "Pending", "Resolved", "Closed")
PRIORITIES = ("Low", "Medium", "High", "Critical")
CATEGORIES = ("Hardware", "Software", "Network", "Access", "Other")


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="requester")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    requested_tickets = db.relationship(
        "Ticket",
        foreign_keys="Ticket.requester_id",
        back_populates="requester",
        cascade="all, delete-orphan",
    )
    assigned_tickets = db.relationship(
        "Ticket", foreign_keys="Ticket.assignee_id", back_populates="assignee"
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(30), nullable=False)
    priority = db.Column(db.String(20), nullable=False, default="Medium")
    status = db.Column(db.String(20), nullable=False, default="Open", index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    requester_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    assignee_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    requester = db.relationship(
        "User", foreign_keys=[requester_id], back_populates="requested_tickets"
    )
    assignee = db.relationship(
        "User", foreign_keys=[assignee_id], back_populates="assigned_tickets"
    )
    comments = db.relationship("Comment", back_populates="ticket", cascade="all, delete-orphan")
    activities = db.relationship("Activity", back_populates="ticket", cascade="all, delete-orphan")


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.String(2000), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    ticket_id = db.Column(
        db.Integer, db.ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False
    )
    author_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    author_name = db.Column(db.String(80), nullable=False)
    ticket = db.relationship("Ticket", back_populates="comments")
    author = db.relationship("User")


class Activity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.String(240), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    ticket_id = db.Column(
        db.Integer, db.ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False
    )
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    actor_name = db.Column(db.String(80), nullable=False)
    ticket = db.relationship("Ticket", back_populates="activities")
    actor = db.relationship("User")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        try:
            payload = json.loads(record.getMessage())
        except json.JSONDecodeError:
            payload = {"message": record.getMessage()}
        return json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                **payload,
            }
        )


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    database_path = Path(app.instance_path, "nexadesk.db").as_posix()
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", f"sqlite:///{database_path}"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    if (
        os.environ.get("APP_ENV") == "production"
        and not os.environ.get("SECRET_KEY")
        and not (test_config and test_config.get("SECRET_KEY"))
    ):
        raise RuntimeError("SECRET_KEY must be set in production.")

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    app.logger.handlers.clear()
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)

    db.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @app.before_request
    def protect_forms():
        request._started_at = time.perf_counter()
        if request.method == "POST":
            expected = session.get("_csrf_token")
            submitted = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
            if not expected or not secrets.compare_digest(expected, submitted or ""):
                abort(400, description="Your session token is invalid. Refresh and try again.")

    @app.after_request
    def log_request(response):
        duration_ms = round(
            (time.perf_counter() - getattr(request, "_started_at", time.perf_counter())) * 1000
        )
        app.logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                }
            )
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    @app.context_processor
    def template_helpers():
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_hex(32)
        return {
            "csrf_token": session["_csrf_token"],
            "statuses": STATUSES,
            "priorities": PRIORITIES,
            "categories": CATEGORIES,
        }

    @app.template_filter("datetime")
    def format_datetime(value):
        if not value:
            return "—"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%b %d, %Y · %I:%M %p UTC")

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def handle_client_error(error):
        return (
            render_template(
                "error.html",
                code=error.code,
                message=error.description if error.code == 400 else error.name,
            ),
            error.code,
        )

    @app.errorhandler(500)
    def handle_server_error(error):
        app.logger.error("Unhandled application error", exc_info=error)
        db.session.rollback()
        return render_template("error.html", code=500, message="An unexpected error occurred."), 500

    @app.get("/health")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            app.logger.exception("Database health check failed")
            return jsonify(status="unhealthy"), 503
        return jsonify(status="healthy"), 200

    @app.cli.command("init-db")
    def init_db():
        """Create the database tables for a fresh installation."""
        db.create_all()
        click.echo("NexaDesk database initialized.")

    def visible_tickets():
        query = Ticket.query
        if current_user.role == "requester":
            query = query.filter_by(requester_id=current_user.id)
        return query

    def ticket_for_current_user(ticket_id):
        ticket = db.get_or_404(Ticket, ticket_id)
        if current_user.role == "requester" and ticket.requester_id != current_user.id:
            abort(404)
        return ticket

    def role_required(*allowed_roles):
        def decorator(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                if current_user.role not in allowed_roles:
                    abort(403)
                return view(*args, **kwargs)

            return wrapped

        return decorator

    @app.get("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if User.query.first() is None:
            return redirect(url_for("setup"))
        return redirect(url_for("login"))

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if User.query.first() is not None:
            return redirect(url_for("login"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if not name or len(name) > 80:
                flash("Enter a name up to 80 characters.", "error")
            elif not valid_email(email):
                flash("Enter a valid email address.", "error")
            elif len(password) < 10:
                flash("Use a password with at least 10 characters.", "error")
            else:
                user = User(name=name, email=email, role="administrator")
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                login_user(user)
                flash("Your administrator account is ready.", "success")
                return redirect(url_for("dashboard"))
        return render_template("setup.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                session.clear()
                login_user(user)
                flash("Welcome back.", "success")
                return redirect(url_for("dashboard"))
            flash("Email or password is incorrect.", "error")
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout():
        logout_user()
        session.clear()
        flash("You have signed out.", "info")
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        scope = visible_tickets()
        counts = {status: scope.filter_by(status=status).count() for status in STATUSES}
        priority_counts = {
            priority: scope.filter_by(priority=priority).count() for priority in PRIORITIES
        }
        recent_activities = (
            Activity.query.join(Ticket)
            .filter(Ticket.id.in_(scope.with_entities(Ticket.id)))
            .order_by(Activity.created_at.desc())
            .limit(8)
            .all()
        )
        return render_template(
            "dashboard.html",
            counts=counts,
            priority_counts=priority_counts,
            total=sum(counts.values()),
            recent_activities=recent_activities,
        )

    @app.get("/tickets")
    @login_required
    def tickets():
        query = visible_tickets()
        search = request.args.get("q", "").strip()[:160]
        status = request.args.get("status", "")
        priority = request.args.get("priority", "")
        category = request.args.get("category", "")
        sort = request.args.get("sort", "newest")
        if search:
            query = query.filter(
                or_(
                    Ticket.title.ilike(f"%{search}%"),
                    Ticket.description.ilike(f"%{search}%"),
                )
            )
        if status in STATUSES:
            query = query.filter_by(status=status)
        if priority in PRIORITIES:
            query = query.filter_by(priority=priority)
        if category in CATEGORIES:
            query = query.filter_by(category=category)
        ordering = {
            "newest": Ticket.created_at.desc(),
            "oldest": Ticket.created_at.asc(),
            "updated": Ticket.updated_at.desc(),
            "priority": case(
                (Ticket.priority == "Critical", 0),
                (Ticket.priority == "High", 1),
                (Ticket.priority == "Medium", 2),
                else_=3,
            ),
        }
        page = request.args.get("page", 1, type=int)
        pagination = query.order_by(ordering.get(sort, Ticket.created_at.desc())).paginate(
            page=max(page, 1), per_page=10, error_out=False
        )
        return render_template(
            "tickets.html",
            pagination=pagination,
            search=search,
            selected_status=status,
            selected_priority=priority,
            selected_category=category,
            selected_sort=sort,
        )

    @app.route("/tickets/new", methods=["GET", "POST"])
    @login_required
    def new_ticket():
        users = (
            User.query.filter(User.role.in_(("administrator", "support agent")))
            .order_by(User.name)
            .all()
        )
        requesters = (
            User.query.order_by(User.name).all()
            if current_user.role != "requester"
            else [current_user]
        )
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            category = request.form.get("category", "")
            priority = request.form.get("priority", "")
            requester_id = current_user.id
            assignee_id = request.form.get("assignee_id", "").strip() or None
            if current_user.role != "requester":
                requester_id = request.form.get("requester_id", type=int)
            errors = []
            if not title or len(title) > 160:
                errors.append("Title is required and must be at most 160 characters.")
            if not description or len(description) > 5000:
                errors.append("Description is required and must be at most 5,000 characters.")
            if category not in CATEGORIES:
                errors.append("Choose a valid category.")
            if priority not in PRIORITIES:
                errors.append("Choose a valid priority.")
            requester = db.session.get(User, requester_id) if requester_id else None
            if requester is None or (
                current_user.role == "requester" and requester.id != current_user.id
            ):
                errors.append("Choose a valid requester.")
            assignee = None
            if assignee_id:
                assignee = db.session.get(User, int(assignee_id)) if assignee_id.isdigit() else None
                if assignee is None or assignee.role not in (
                    "administrator",
                    "support agent",
                ):
                    errors.append("Choose a valid support agent.")
            if errors:
                for error in errors:
                    flash(error, "error")
            else:
                ticket = Ticket(
                    title=title,
                    description=description,
                    category=category,
                    priority=priority,
                    requester=requester,
                    assignee=assignee,
                )
                db.session.add(ticket)
                db.session.flush()
                db.session.add(
                    Activity(
                        ticket=ticket,
                        actor=current_user,
                        actor_name=current_user.name,
                        message="created this ticket",
                    )
                )
                db.session.commit()
                flash(f"Ticket #{ticket.id:04d} was created.", "success")
                return redirect(url_for("ticket_detail", ticket_id=ticket.id))
        return render_template("new_ticket.html", users=users, requesters=requesters)

    @app.get("/tickets/<int:ticket_id>")
    @login_required
    def ticket_detail(ticket_id):
        ticket = ticket_for_current_user(ticket_id)
        activities = (
            Activity.query.filter_by(ticket_id=ticket.id).order_by(Activity.created_at.asc()).all()
        )
        users = (
            User.query.filter(User.role.in_(("administrator", "support agent")))
            .order_by(User.name)
            .all()
        )
        return render_template(
            "ticket_detail.html",
            ticket=ticket,
            activities=activities,
            users=users,
        )

    @app.post("/tickets/<int:ticket_id>/status")
    @login_required
    @role_required("administrator", "support agent")
    def update_status(ticket_id):
        ticket = ticket_for_current_user(ticket_id)
        new_status = request.form.get("status", "")
        if new_status not in STATUSES:
            flash("Choose a valid status.", "error")
        elif ticket.status != new_status:
            previous = ticket.status
            ticket.status = new_status
            db.session.add(
                Activity(
                    ticket=ticket,
                    actor=current_user,
                    actor_name=current_user.name,
                    message=f"changed status from {previous} to {new_status}",
                )
            )
            db.session.commit()
            flash("Ticket status updated.", "success")
        return redirect(url_for("ticket_detail", ticket_id=ticket.id))

    @app.post("/tickets/<int:ticket_id>/assignee")
    @login_required
    @role_required("administrator", "support agent")
    def update_assignee(ticket_id):
        ticket = ticket_for_current_user(ticket_id)
        raw_assignee_id = request.form.get("assignee_id", "").strip()
        assignee = None
        if raw_assignee_id:
            assignee = (
                db.session.get(User, int(raw_assignee_id)) if raw_assignee_id.isdigit() else None
            )
        if raw_assignee_id and (
            assignee is None or assignee.role not in ("administrator", "support agent")
        ):
            flash("Choose a valid support agent.", "error")
        else:
            previous = ticket.assignee.name if ticket.assignee else "Unassigned"
            ticket.assignee = assignee
            current_name = assignee.name if assignee else "Unassigned"
            if previous != current_name:
                db.session.add(
                    Activity(
                        ticket=ticket,
                        actor=current_user,
                        actor_name=current_user.name,
                        message=f"assigned this ticket to {current_name}",
                    )
                )
                db.session.commit()
                flash("Assignment updated.", "success")
        return redirect(url_for("ticket_detail", ticket_id=ticket.id))

    @app.post("/tickets/<int:ticket_id>/comments")
    @login_required
    def add_comment(ticket_id):
        ticket = ticket_for_current_user(ticket_id)
        body = request.form.get("body", "").strip()
        if not body or len(body) > 2000:
            flash("Comment is required and must be at most 2,000 characters.", "error")
            return redirect(url_for("ticket_detail", ticket_id=ticket.id))
        db.session.add(
            Comment(
                ticket=ticket,
                body=body,
                author=current_user,
                author_name=current_user.name,
            )
        )
        db.session.add(
            Activity(
                ticket=ticket,
                actor=current_user,
                actor_name=current_user.name,
                message="added a comment",
            )
        )
        db.session.commit()
        flash("Comment added.", "success")
        return redirect(url_for("ticket_detail", ticket_id=ticket.id))

    @app.route("/users", methods=["GET", "POST"])
    @login_required
    @role_required("administrator")
    def users():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            role = request.form.get("role", "")
            if not name or len(name) > 80:
                flash("Enter a name up to 80 characters.", "error")
            elif not valid_email(email):
                flash("Enter a valid email address.", "error")
            elif User.query.filter_by(email=email).first():
                flash("An account with that email already exists.", "error")
            elif len(password) < 10:
                flash("Use a password with at least 10 characters.", "error")
            elif role not in ROLES:
                flash("Choose a valid role.", "error")
            else:
                user = User(name=name, email=email, role=role)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                flash(f"Account created for {user.name}.", "success")
                return redirect(url_for("users"))
        return render_template(
            "users.html", users=User.query.order_by(User.name).all(), roles=ROLES
        )

    return app


def valid_email(email):
    return bool(email and len(email) <= 254 and "@" in email and "." in email.rsplit("@", 1)[-1])


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)

import re

import pytest

from app import Activity, Comment, Ticket, User, create_app, db


@pytest.fixture
def app():
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-only-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "WTF_CSRF_ENABLED": False,
        }
    )
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def csrf_token(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match, "page should contain a CSRF token"
    return match.group(1)


def add_user(app, name, email, role="requester", password="strong-password-123"):
    with app.app_context():
        user = User(name=name, email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user.id


def sign_in(client, email, password="strong-password-123"):
    response = client.get("/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf_token(response),
            "email": email,
            "password": password,
        },
    )
    assert response.status_code == 302
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    return csrf_token(dashboard)


def add_ticket(app, requester_id, **overrides):
    values = {
        "title": "Cannot access shared drive",
        "description": "The shared drive shows an access denied message.",
        "category": "Access",
        "priority": "High",
        "status": "Open",
        "requester_id": requester_id,
    }
    values.update(overrides)
    with app.app_context():
        ticket = Ticket(**values)
        db.session.add(ticket)
        db.session.commit()
        requester = db.session.get(User, requester_id)
        db.session.add(
            Activity(
                ticket=ticket,
                actor=requester,
                actor_name=requester.name,
                message="created this ticket",
            )
        )
        db.session.commit()
        return ticket.id


def test_first_run_creates_an_administrator_with_a_hashed_password(client, app):
    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/setup")

    setup = client.get("/setup")
    response = client.post(
        "/setup",
        data={
            "csrf_token": csrf_token(setup),
            "name": "Avery Admin",
            "email": "avery@example.test",
            "password": "a-unique-long-password",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    with app.app_context():
        administrator = User.query.filter_by(email="avery@example.test").one()
        assert administrator.role == "administrator"
        assert administrator.password_hash != "a-unique-long-password"
        assert administrator.check_password("a-unique-long-password")
    assert client.get("/setup").headers["Location"].endswith("/login")


def test_setup_validates_password_and_email(client, app):
    response = client.get("/setup")
    response = client.post(
        "/setup",
        data={
            "csrf_token": csrf_token(response),
            "name": "Admin",
            "email": "not-an-email",
            "password": "short",
        },
    )
    assert response.status_code == 200
    assert b"Enter a valid email address" in response.data
    with app.app_context():
        assert User.query.count() == 0


def test_login_rejects_bad_credentials(client, app):
    add_user(app, "Jordan", "jordan@example.test")
    response = client.get("/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf_token(response),
            "email": "jordan@example.test",
            "password": "wrong-password",
        },
    )
    assert response.status_code == 200
    assert b"Email or password is incorrect" in response.data


def test_ticket_create_detail_comment_and_activity(client, app):
    requester_id = add_user(app, "Riley", "riley@example.test")
    token = sign_in(client, "riley@example.test")
    response = client.post(
        "/tickets/new",
        data={
            "csrf_token": token,
            "title": "Laptop will not start",
            "description": "The power light flashes but the device will not boot.",
            "category": "Hardware",
            "priority": "Critical",
        },
    )
    assert response.status_code == 302
    ticket_id = int(response.headers["Location"].rsplit("/", 1)[-1])

    detail = client.get(f"/tickets/{ticket_id}")
    assert detail.status_code == 200
    assert b"Laptop will not start" in detail.data
    assert b"Riley" in detail.data
    token = csrf_token(detail)
    response = client.post(
        f"/tickets/{ticket_id}/comments",
        data={"csrf_token": token, "body": "I also tried a different power adapter."},
    )
    assert response.status_code == 302

    with app.app_context():
        ticket = db.session.get(Ticket, ticket_id)
        assert ticket.requester_id == requester_id
        assert ticket.status == "Open"
        assert Comment.query.filter_by(ticket_id=ticket_id).one().body.startswith("I also tried")
        assert [event.message for event in Activity.query.filter_by(ticket_id=ticket_id)] == [
            "created this ticket",
            "added a comment",
        ]


def test_ticket_input_is_validated(client, app):
    add_user(app, "Riley", "riley@example.test")
    token = sign_in(client, "riley@example.test")
    response = client.post(
        "/tickets/new",
        data={
            "csrf_token": token,
            "title": "",
            "description": "issue",
            "category": "Made up",
            "priority": "Urgent",
        },
    )
    assert response.status_code == 200
    assert b"Title is required" in response.data
    with app.app_context():
        assert Ticket.query.count() == 0


def test_requesters_only_see_their_own_tickets(client, app):
    first_id = add_user(app, "Morgan", "morgan@example.test")
    second_id = add_user(app, "Taylor", "taylor@example.test")
    own_ticket = add_ticket(app, first_id, title="My request")
    other_ticket = add_ticket(app, second_id, title="Private request")

    sign_in(client, "morgan@example.test")
    listing = client.get("/tickets")
    assert b"My request" in listing.data
    assert b"Private request" not in listing.data
    assert client.get(f"/tickets/{own_ticket}").status_code == 200
    assert client.get(f"/tickets/{other_ticket}").status_code == 404


def test_support_agent_can_change_status_and_assignments(client, app):
    requester_id = add_user(app, "Sam", "sam@example.test")
    agent_id = add_user(app, "Alex", "alex@example.test", role="support agent")
    ticket_id = add_ticket(app, requester_id)
    token = sign_in(client, "alex@example.test")
    response = client.post(
        f"/tickets/{ticket_id}/status",
        data={"csrf_token": token, "status": "In Progress"},
    )
    assert response.status_code == 302
    detail = client.get(f"/tickets/{ticket_id}")
    token = csrf_token(detail)
    response = client.post(
        f"/tickets/{ticket_id}/assignee",
        data={"csrf_token": token, "assignee_id": str(agent_id)},
    )
    assert response.status_code == 302
    with app.app_context():
        ticket = db.session.get(Ticket, ticket_id)
        assert ticket.status == "In Progress"
        assert ticket.assignee_id == agent_id
        assert [event.message for event in ticket.activities] == [
            "created this ticket",
            "changed status from Open to In Progress",
            "assigned this ticket to Alex",
        ]


def test_requester_cannot_change_ticket_status(client, app):
    requester_id = add_user(app, "Jamie", "jamie@example.test")
    ticket_id = add_ticket(app, requester_id)
    token = sign_in(client, "jamie@example.test")
    response = client.post(
        f"/tickets/{ticket_id}/status",
        data={"csrf_token": token, "status": "Resolved"},
    )
    assert response.status_code == 403
    with app.app_context():
        assert db.session.get(Ticket, ticket_id).status == "Open"


def test_search_filters_sorting_and_pagination(client, app):
    requester_id = add_user(app, "Casey", "casey@example.test")
    for index in range(12):
        add_ticket(
            app,
            requester_id,
            title=f"Network request {index:02d}",
            category="Network",
            priority="Critical" if index == 0 else "Low",
            status="Resolved" if index == 0 else "Open",
        )
    sign_in(client, "casey@example.test")
    first_page = client.get("/tickets?q=Network&category=Network&sort=priority")
    assert first_page.status_code == 200
    assert b"Network request 00" in first_page.data
    assert b"Page 1 of 2" in first_page.data
    assert b"Next" in first_page.data
    second_page = client.get("/tickets?q=Network&category=Network&page=2")
    assert second_page.status_code == 200
    assert b"Page 2 of 2" in second_page.data
    filtered = client.get("/tickets?status=Resolved")
    assert b"Network request 00" in filtered.data
    assert b"Network request 01" not in filtered.data


def test_administrator_can_create_accounts_but_agents_cannot_manage_people(client, app):
    add_user(app, "Admin", "admin@example.test", role="administrator")
    token = sign_in(client, "admin@example.test")
    response = client.post(
        "/users",
        data={
            "csrf_token": token,
            "name": "Lee Agent",
            "email": "lee@example.test",
            "password": "another-strong-password",
            "role": "support agent",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        assert User.query.filter_by(email="lee@example.test").one().role == "support agent"

    logout_token = csrf_token(client.get("/dashboard"))
    client.post("/logout", data={"csrf_token": logout_token})
    sign_in(client, "lee@example.test", "another-strong-password")
    assert client.get("/users").status_code == 403


def test_all_post_routes_require_a_csrf_token(client, app):
    add_user(app, "Riley", "riley@example.test")
    sign_in(client, "riley@example.test")
    response = client.post("/logout")
    assert response.status_code == 400
    assert b"session token is invalid" in response.data


def test_health_endpoint_checks_database(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "healthy"}

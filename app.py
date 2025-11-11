import os
import random
import string
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, jsonify,
    url_for, flash, session, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import BadRequest

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc
from sqlalchemy.sql import func

# ---------- App / Config ----------
app = Flask(__name__)

# Use environment secret if available; fallback to a random secret for dev (not for prod)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)

# DB config
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'postgresql://user:djDBd9T2RjVHhDaNRl6xbVRTxy2GAx9Y@dpg-d3r3c70gjchc73bpcobg-a.oregon-postgres.render.com/bytejamvote'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Admin credentials: pull password from env if possible (safer than hardcoding)
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "ihccadminjslj")
_admin_password_plain = os.environ.get("ADMIN_PASSWORD", "jslj!1238!")  # replace in prod
_ADMIN_PASSWORD_HASH = generate_password_hash(_admin_password_plain)

# Allowed IPs: can be set as comma-separated env var (e.g. "127.0.0.1,167.142.113.82")
_allowed_env = os.environ.get("ALLOWED_CONN", "167.142.113.82,127.0.0.1")
allowed_conn = [ip.strip() for ip in _allowed_env.split(",") if ip.strip()]

# Helper to permit all (for local dev) when explicitly enabled
ALLOW_ALL = os.environ.get("ALLOW_ALL", "false").lower() in ("1", "true", "yes")

db = SQLAlchemy(app)


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.Enum('student', 'guest', 'coach', 'company', name='role_enum'), nullable=False)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    pin = db.Column(db.String(4), nullable=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=True)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=True)
    ip_address = db.Column(db.String(50), nullable=False)

    company = db.relationship('Company', backref='users', lazy=True)
    team = db.relationship('Team', backref='team_members', lazy=True)

    def __init__(self, role, first_name, last_name, pin=None, company_id=None, team_id=None, ip_address=None):
        self.role = role
        self.first_name = first_name
        self.last_name = last_name
        self.pin = pin
        self.company_id = company_id
        self.team_id = team_id
        self.ip_address = ip_address


class Team(db.Model):
    __tablename__ = 'teams'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    division = db.Column(db.Enum('Bits', 'Bytes', 'IHCC', name='division_enum'), nullable=False)

    def __repr__(self):
        return f'<Team {self.name}>'


class Company(db.Model):
    __tablename__ = 'company'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    pin = db.Column(db.String(4), nullable=False)

    def __repr__(self):
        return f'<Company {self.name}>'


class Vote(db.Model):
    __tablename__ = 'votes'

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    category = db.Column(db.String(50), nullable=True)
    score = db.Column(db.Integer, nullable=True)

    team = db.relationship('Team', backref='votes', lazy=True)
    user = db.relationship('User', backref='votes', lazy=True)

    __table_args__ = (db.UniqueConstraint('user_id', 'team_id', 'category', name='unique_user_vote'),)

    def __repr__(self):
        return f'<Vote User {self.user_id} - Team {self.team_id}: {self.category} - {self.score}>'


class VotingSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    voting_start = db.Column(db.DateTime, nullable=False)
    voting_end = db.Column(db.DateTime, nullable=False)


# ---------- Helpers ----------
def generate_pin(length: int = 4) -> str:
    return ''.join(random.choices(string.digits, k=length))


def _verify_admin(username: str, password: str) -> bool:
    return username == ADMIN_USERNAME and check_password_hash(_ADMIN_PASSWORD_HASH, password)


def get_client_ip():
    # respect X-Forwarded-For if sitting behind proxy (Render / Heroku)
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        # X-Forwarded-For may be a comma separated list; first is client
        ip = forwarded.split(',')[0].strip()
    else:
        ip = request.remote_addr or ''
    return ip


def ip_allowed(ip: str) -> bool:
    if ALLOW_ALL:
        return True
    if not ip:
        return False
    # exact matches or prefix matches (if you intentionally used prefix like "167.142")
    for allowed in allowed_conn:
        if ip == allowed or ip.startswith(allowed):
            return True
    return False


# ---------- Request guards ----------
@app.before_request
def limit_remote_addr():
    # allow admin-login and static assets even if IP restricted so admin can manage
    public_paths = ['/admin-login', '/static/']
    if any(request.path.startswith(p) for p in public_paths):
        return

    client_ip = get_client_ip()
    # logging only in debug mode (not printing in prod)
    if not ip_allowed(client_ip):
        abort(403)


# ---------- Auth decorators ----------
def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Admin login required.", "warning")
            return redirect(url_for("admin_login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapper


# ---------- Routes ----------
@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    # allow login page even if voting window closed
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password", "") or ""
        if _verify_admin(username, password):
            session["is_admin"] = True
            flash("Welcome, admin.", "success")
            dest = request.args.get("next") or url_for("admin_panel")
            return redirect(dest)
        else:
            flash("Invalid admin credentials.", "danger")
    return render_template("admin_login.html")


@app.route("/admin-logout")
def admin_logout():
    session.pop("is_admin", None)
    flash("Logged out.", "info")
    return redirect(url_for("admin_login"))


@app.route('/admin')
@admin_required
def admin_panel():
    companies = Company.query.all()

    company_data = {
        c.name: {
            "pin": c.pin,
            "members": [f"{u.first_name} {u.last_name}" for u in c.users]
        }
        for c in companies
    }

    teams = Team.query.all()

    # Summarize votes
    raw_results = (
        db.session.query(
            Vote.team_id,
            Team.name.label('team_name'),
            func.sum(Vote.score).label('total_score'),
            func.count(func.distinct(Vote.user_id)).label('vote_count')
        )
        .join(Team, Team.id == Vote.team_id)
        .group_by(Vote.team_id, Team.name)
        .all()
    )

    total_all_scores = sum((r.total_score or 0) for r in raw_results)
    total_all_votes = sum((r.vote_count or 0) for r in raw_results)
    c = total_all_scores / total_all_votes if total_all_votes > 0 else 0

    # Bayes baseline
    m = 10

    team_scores = {}
    for r in raw_results:
        team_id = r.team_id
        team_name = r.team_name
        v = r.vote_count or 0
        R = (r.total_score / v) if v > 0 else 0
        adjusted_score = (v * R + m * c) / (v + m) if (v + m) > 0 else 0
        team_scores[team_id] = {
            'team_name': team_name,
            'raw_average': round(R, 4),
            'adjusted_score': round(adjusted_score, 4),
            'vote_count': v
        }

    sorted_scores = sorted(team_scores.items(), key=lambda x: x[1]['adjusted_score'], reverse=True)
    enumerated_scores = list(enumerate(sorted_scores, start=1))

    team_data = {}
    for t in teams:
        division = t.division
        if division not in team_data:
            team_data[division] = {}
        # return member objects if template expects them, otherwise names:
        team_data[division][t.name] = {
            "division": t.division,
            "members": [f"{u.first_name} {u.last_name}" for u in User.query.filter_by(team_id=t.id).all()]
        }

    return render_template(
        'admin.html',
        companies=company_data,
        teams=team_data,
        team_scores=enumerated_scores
    )


@app.route("/create")
def create():
    return render_template("index.html", fresh_pin=generate_pin())


@app.route('/save_voting_settings', methods=['POST'])
@admin_required
def save_voting_settings():
    voting_start = request.form.get('voting_start')
    voting_end = request.form.get('voting_end')

    if not (voting_start and voting_end):
        flash("Both start and end times are required.", "danger")
        return redirect(url_for('admin_panel'))

    try:
        start_dt = datetime.strptime(voting_start, '%Y-%m-%dT%H:%M')
        end_dt = datetime.strptime(voting_end, '%Y-%m-%dT%H:%M')
    except ValueError:
        flash("Invalid date format. Use the date-time picker.", "danger")
        return redirect(url_for('admin_panel'))

    if start_dt >= end_dt:
        flash("Voting start must be before voting end.", "danger")
        return redirect(url_for('admin_panel'))

    new_settings = VotingSettings(voting_start=start_dt, voting_end=end_dt)
    db.session.add(new_settings)
    db.session.commit()

    flash("Voting window saved.", "success")
    return redirect(url_for('admin_panel'))


@app.route('/submit_vote', methods=['POST'])
def submit_vote():
    team_name = (request.form.get('team') or "").strip()
    # form fields might be None; normalize
    creativity = request.form.get('creativity')
    visualdesign = request.form.get('visualdesign')
    professionalism = request.form.get('professionalism')
    communication = request.form.get('communication')
    performance = request.form.get('performance')

    if 'user_id' not in session:
        flash("You must be logged in to vote.", "danger")
        return redirect(url_for('login'))

    user_id = session['user_id']

    team_row = db.session.query(Team.id).filter_by(name=team_name).first()
    if not team_row:
        flash("Invalid team name.", "danger")
        return redirect(url_for('dashboard'))
    team_id = team_row[0]

    categories = ["Creativity", "Visual Design", "Professionalism", "Communication", "Performance"]
    scores = [creativity, visualdesign, professionalism, communication, performance]

    # convert and validate scores
    parsed_scores = []
    try:
        for s in scores:
            if s is None or s == "":
                raise ValueError("All score fields are required.")
            val = int(s)
            if val < 0 or val > 10:
                raise ValueError("Scores must be integers between 0 and 10.")
            parsed_scores.append(val)
    except ValueError as ve:
        flash(str(ve), "danger")
        return redirect(url_for('dashboard'))

    try:
        for category, score_val in zip(categories, parsed_scores):
            existing_vote = Vote.query.filter_by(user_id=user_id, team_id=team_id, category=category).first()
            if existing_vote:
                flash(f"You have already voted for {category} in this team.", "danger")
                return redirect(url_for('dashboard'))

            new_vote = Vote(team_id=team_id, user_id=user_id, category=category, score=score_val)
            db.session.add(new_vote)

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Error saving votes")
        flash("There was an error saving your vote. Please try again.", "danger")
        return redirect(url_for('dashboard'))

    flash("Your vote has been submitted!", "success")
    return redirect(url_for('dashboard'))


@app.route("/register_user", methods=["POST"])
def register_user():
    """
    Registration flow:
      - user_type '1' = existing student/coach/company (they should provide first/last and company_pin)
          -> find company by pin, find user with that name & company_id. If found, attach to session.
      - user_type '2' = guest: create a new guest user.
    """
    user_type = request.form.get('user_type')

    # Get remote IP safely
    ip_address = get_client_ip()

    if user_type == '1':  # existing (company/coach/student)
        first_name = (request.form.get('m_first_name') or "").strip()
        last_name = (request.form.get('m_last_name') or "").strip()
        company_pin = (request.form.get('company_pin') or "").strip()

        if not (first_name and last_name and company_pin):
            flash("Name and company pin are required.", "danger")
            return redirect(url_for('create'))

        company = Company.query.filter_by(pin=company_pin).first()
        if not company:
            flash("Company not found for that pin.", "danger")
            return redirect(url_for('create'))

        user = User.query.filter_by(first_name=first_name, last_name=last_name, company_id=company.id).first()
        if user:
            session['user_id'] = user.id
            session['first_name'] = user.first_name
            session['last_name'] = user.last_name
            session['pin'] = user.pin
            session['role'] = user.role
            flash("Welcome back!", "success")
            return redirect(url_for('dashboard'))
        else:
            flash("User not found in that company. If you should be there, ask an admin to add you.", "danger")
            return redirect(url_for('create'))

    elif user_type == '2':  # guest
        try:
            pin = (request.form.get('pin') or "").strip()
            # prefer explicit form field names for guest values
            first_name = (request.form.get('first_name') or "").strip()
            last_name = (request.form.get('last_name') or "").strip()
            company_id = request.form.get('company_id', None)
        except BadRequest:
            flash("Invalid form submission.", "danger")
            return redirect(url_for('create'))

        if not (first_name and last_name):
            flash("First and last name are required for guest registration.", "danger")
            return redirect(url_for('create'))

        # Basic sanitization: pin should be 4 digits if provided
        if pin and (not pin.isdigit() or len(pin) != 4):
            flash("Invalid PIN format; must be 4 digits.", "danger")
            return redirect(url_for('create'))

        new_user = User(
            role='guest',
            first_name=first_name,
            last_name=last_name,
            pin=pin or None,
            company_id=company_id,
            ip_address=ip_address or ""
        )

        try:
            db.session.add(new_user)
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Error creating guest user")
            flash("Could not create guest account. Contact admin.", "danger")
            return redirect(url_for('create'))

        session['user_id'] = new_user.id
        session['first_name'] = new_user.first_name
        session['last_name'] = new_user.last_name
        session['pin'] = new_user.pin
        flash("Guest registered.", "success")
        return redirect(url_for('dashboard'))

    else:
        flash("Invalid user type selected!", "danger")
        return redirect(url_for('create'))


@app.route("/")
def dashboard():
    settings = VotingSettings.query.order_by(desc(VotingSettings.id)).first()

    if not settings:
        flash("Voting settings have not been configured yet.", "warning")
        return redirect(url_for('not_allowed'))

    voting_start = settings.voting_start
    voting_end = settings.voting_end
    current_time = datetime.now()

    if current_time < voting_start or current_time > voting_end:
        return redirect(url_for('not_allowed'))

    if 'user_id' in session:
        user_id = session['user_id']
        first_name = session.get('first_name')
        last_name = session.get('last_name')
        pin = session.get('pin')

        companies = Company.query.all()
        company_data = {
            c.name: {
                "pin": c.pin,
                "members": [f"{u.first_name} {u.last_name}" for u in c.users]
            }
            for c in companies
        }

        teams = Team.query.all()

        team_data = {}
        for t in teams:
            division = t.division
            if division not in team_data:
                team_data[division] = {}

            user_vote = Vote.query.filter_by(user_id=user_id, team_id=t.id).first()
            has_voted = True if user_vote else False

            team_data[division][t.name] = {
                "division": t.division,
                "members": [f"{u.first_name} {u.last_name}" for u in User.query.filter_by(team_id=t.id).all()],
                "has_voted": has_voted
            }

        return render_template('dashboard.html', first_name=first_name, last_name=last_name, company_pin=pin, companies=company_data, teams=team_data)

    flash('You must be logged in to view the dashboard.', 'warning')
    return redirect('/create')


@app.route('/delete_data', methods=['POST'])
@admin_required
def delete_data():
    try:
        # respect checkboxes; test for keys
        if 'delete_votes' in request.form:
            db.session.query(Vote).delete()

        if 'delete_users' in request.form:
            db.session.query(User).delete()

        if 'delete_teams' in request.form:
            db.session.query(Team).delete()

        if 'delete_companies' in request.form:
            db.session.query(Company).delete()

        db.session.commit()
        flash("Requested data deleted.", "success")
    except Exception:
        db.session.rollback()
        app.logger.exception("Error deleting data")
        flash("There was an error deleting data.", "danger")
    return redirect(url_for('admin_panel'))


@app.route('/not_allowed')
def not_allowed():
    return "Voting is not allowed at this time."


@app.route('/submit', methods=['POST'])
@admin_required
def handle_form():
    action = request.form.get('action')
    entity_type = request.form.get('type')

    try:
        if entity_type == 'company':
            if action == 'add':
                name = (request.form.get('company_name') or "").strip()
                if not name:
                    flash("Company name is required.", "danger")
                    return redirect(url_for('admin_panel'))
                new_company = Company(name=name, pin=generate_pin())
                db.session.add(new_company)
                db.session.commit()
                flash(f"Company '{name}' added successfully!", "success")

            elif action == 'remove':
                company = Company.query.filter_by(name=request.form['company_name']).first()
                if company:
                    db.session.delete(company)
                    db.session.commit()
                    flash(f"Company '{company.name}' removed successfully!", "success")
                else:
                    flash("Company not found!", "danger")

            elif action == 'add_member':
                first_name = (request.form.get('first_name') or "").strip()
                last_name = (request.form.get('last_name') or "").strip()
                pin = (request.form.get('pin') or "").strip()
                company = Company.query.filter_by(name=request.form['company']).first()

                if not (first_name and last_name and pin):
                    flash("Member name and pin are required.", "danger")
                    return redirect(url_for('admin_panel'))

                if company:
                    new_user = User(
                        role='company',
                        first_name=first_name,
                        last_name=last_name,
                        pin=pin,
                        company_id=company.id,
                        ip_address=get_client_ip()
                    )
                    db.session.add(new_user)
                    db.session.commit()
                    flash(f"Member '{first_name} {last_name}' added to '{company.name}'!", "success")
                else:
                    flash("Company not found!", "danger")

            elif action == 'remove_member':
                parts = request.form['member_name'].split()
                if len(parts) < 2:
                    flash("Please provide both first and last name.", "danger")
                    return redirect(url_for('admin_panel'))
                first, last = parts[0], parts[1]
                user = User.query.filter_by(first_name=first, last_name=last).first()
                if user:
                    db.session.delete(user)
                    db.session.commit()
                    flash(f"Member '{user.first_name} {user.last_name}' removed!", "success")
                else:
                    flash("Member not found!", "danger")

        elif entity_type == 'team':
            if action == 'add':
                name = (request.form.get('team_name') or "").strip()
                division = request.form.get('division')
                if not (name and division):
                    flash("Team name and division are required.", "danger")
                    return redirect(url_for('admin_panel'))
                new_team = Team(name=name, division=division)
                db.session.add(new_team)
                db.session.commit()
                flash(f"Team '{name}' added successfully!", "success")

            elif action == 'remove':
                team = Team.query.filter_by(name=request.form['team_name']).first()
                if team:
                    db.session.delete(team)
                    db.session.commit()
                    flash(f"Team '{team.name}' removed successfully!", "success")
                else:
                    flash("Team not found!", "danger")

            elif action in ['add_member', 'add_coach']:
                first_name = (request.form.get('first_name') or "").strip()
                last_name = (request.form.get('last_name') or "").strip()
                role_input = request.form.get('role', 'member')
                team = Team.query.filter_by(name=request.form['team_name']).first()

                if not (first_name and last_name and team):
                    flash("Member name and team are required.", "danger")
                    return redirect(url_for('admin_panel'))

                pin = generate_pin()
                role = 'coach' if role_input.lower() == 'coach' else 'student'
                new_user = User(
                    role=role,
                    first_name=first_name,
                    last_name=last_name,
                    pin=pin,
                    team_id=team.id,
                    ip_address=get_client_ip()
                )
                db.session.add(new_user)
                db.session.commit()
                flash(f"{role.capitalize()} '{first_name} {last_name}' added to '{team.name}'!", "success")

            elif action == 'remove_member':
                parts = request.form['member_name'].split()
                if len(parts) < 2:
                    flash("Please provide both first and last name.", "danger")
                    return redirect(url_for('admin_panel'))
                first, last = parts[0], parts[1]
                team = Team.query.filter_by(name=request.form['team_name']).first()
                if not team:
                    flash("Team not found.", "danger")
                    return redirect(url_for('admin_panel'))

                user = User.query.filter_by(first_name=first, last_name=last, team_id=team.id).first()
                if user:
                    db.session.delete(user)
                    db.session.commit()
                    flash(f"Member '{user.first_name} {user.last_name}' removed from '{request.form['team_name']}'!",
                          "success")
                else:
                    flash("Member not found in the team!", "danger")

    except Exception:
        db.session.rollback()
        app.logger.exception("Error handling admin submit")
        flash("An error occurred processing your request.", "danger")

    return redirect('/')


# ---------- Start ----------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    # Turn off debug for production. Use env var FLASK_DEBUG if needed.
    debug_mode = os.environ.get("FLASK_DEBUG", "true").lower() in ("1", "true", "yes")
    app.run(debug=debug_mode)

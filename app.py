import os
from datetime import datetime

from flask import Flask, render_template, request, redirect, jsonify, url_for, flash, session, abort
from flask_sqlalchemy import SQLAlchemy
import random
import string
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash


from sqlalchemy import desc
from sqlalchemy.sql import func

# might still use this, don't need it right now
from werkzeug.exceptions import BadRequest

app = Flask(__name__)
app.secret_key = "dsfsf"

# will need to change this to match render database settings, which is broken right now
# october 18 - not broken, service got suspended -_-
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'postgresql://user:djDBd9T2RjVHhDaNRl6xbVRTxy2GAx9Y@dpg-d3r3c70gjchc73bpcobg-a.oregon-postgres.render.com/bytejamvote'
)

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# --- Admin auth (temporary) ---
ADMIN_USERNAME = "ihccadminjslj"
# You can change this to any short-lived pass; hashing keeps it out of memory in plain text
_ADMIN_PASSWORD_HASH = generate_password_hash("jslj!1238!")

def _verify_admin(username: str, password: str) -> bool:
    return username == ADMIN_USERNAME and check_password_hash(_ADMIN_PASSWORD_HASH, password)

def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Admin login required.", "warning")
            return redirect(url_for("admin_login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapper

db = SQLAlchemy(app)

# this should be indian hills and local for my testing, but indian hills swaps i believe, so could
# potentially be dead in water
allowed_conn = ['167.142.113.82', '127.0.0.1']

class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.Enum('student', 'guest', 'coach', 'company', name='role_enum'), nullable=False)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    pin = db.Column(db.String(4), nullable=True)  # all users will get pins now
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=True)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=True)
    ip_address = db.Column(db.String(50), nullable=False) # need to change to device IP

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


# Vote Model
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

# currently random 4 digits, can change easily
def generate_pin():
    return ''.join(random.choices(string.digits, k=4))


@app.before_request
def limit_remote_addr():
    client_ip = request.remote_addr
    print(client_ip)
    if not any(client_ip.startswith(ip) for ip in allowed_conn):
        abort(403)  # Forbidden

# admin won't be landing page later
@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    # allow login page even if voting window closed
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
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

    # this will Collect total scores the unique vote counts and team names
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

    # this is hopefully (i think) going to calculate global average rating
    total_all_scores = sum(r.total_score for r in raw_results)
    total_all_votes = sum(r.vote_count for r in raw_results)
    c = total_all_scores / total_all_votes if total_all_votes > 0 else 0

    # all this is a mini vote to perform calc, without it.. this calc doesn't function and we have to use a hard val
    m = 10

    # this is calculated adjusted scores based on the bayesian calc method. It should be right,
    # I've never used this calc until now, I just know I researched it for a few hours while trying to implement
    # 4 months back
    team_scores = {}
    for r in raw_results:
        team_id = r.team_id
        team_name = r.team_name
        v = r.vote_count
        R = r.total_score / v if v > 0 else 0
        adjusted_score = (v * R + m * c) / (v + m)
        team_scores[team_id] = {
            'team_name': team_name,
            'raw_average': round(R, 4),
            'adjusted_score': round(adjusted_score, 4),
            'vote_count': v
        }

    # sorting da scores
    sorted_scores = sorted(team_scores.items(), key=lambda x: x[1]['adjusted_score'], reverse=True)
    enumerated_scores = list(enumerate(sorted_scores, start=1))

    team_data = {}
    for t in teams:
        division = t.division
        if division not in team_data:
            team_data[division] = {}
        team_data[division][t.name] = {
            "division": t.division,
            "members": User.query.filter_by(team_id=t.id).all()
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

    if voting_start and voting_end:
        start_dt = datetime.strptime(voting_start, '%Y-%m-%dT%H:%M')
        end_dt = datetime.strptime(voting_end, '%Y-%m-%dT%H:%M')

        new_settings = VotingSettings(voting_start=start_dt, voting_end=end_dt)
        db.session.add(new_settings)
        db.session.commit()

        return redirect(url_for('admin_panel'))

    return "Invalid data", 400


@app.route('/submit_vote', methods=['POST'])
def submit_vote():
    team_name = request.form.get('team')
    creativity = request.form.get('creativity')
    visualdesign = request.form.get('visualdesign')  # correct
    professionalism = request.form.get('professionalism')
    communication = request.form.get('communication')  # typo fixed
    performance = request.form.get('performance')

    # another check to ensure user is logged in
    if 'user_id' not in session:
        flash("You must be logged in to vote.", "danger")
        return redirect(url_for('login'))

    user_id = session['user_id']

    # grab the team name where form was pulled
    team_row = db.session.query(Team.id).filter_by(name=team_name).first()

    if team_row:
        team_id = team_row[0] # pulling id if it exists to add to Vote table
    else:
        flash("Invalid team name.", "danger")
        return redirect(url_for('dashboard'))

    categories = ["Creativity", "Visual Design", "Professionalism", "Communication", "Performance"]
    scores = [creativity, visualdesign, professionalism, communication, performance]

    for category, score in zip(categories, scores):
        existing_vote = Vote.query.filter_by(user_id=user_id, team_id=team_id, category=category).first()

        if existing_vote:
            flash(f"You have already voted for {category} in this team.", "danger")
            return redirect(url_for('dashboard'))

        # adding new vote, only happens if user hasn't already voted yet
        new_vote = Vote(team_id=team_id, user_id=user_id, category=category, score=score)
        db.session.add(new_vote)

    db.session.commit()

    flash("Your vote has been submitted!", "success")
    return redirect(url_for('dashboard'))


@app.route("/register_user", methods=["POST"])
def register_user():
    # 1 - student/coach/company
    # 2 - guest
    user_type = request.form.get('user_type')

    first_name = request.form.get('m_first_name')
    last_name = request.form.get('m_last_name')
    ip_address = request.form.get('ip_address')

    if user_type == '1': # company/coach/student
        company_pin = request.form.get('company_pin')
        user = User.query.filter_by(first_name=first_name, last_name=last_name, pin=company_pin).first()
        if user:
            session['user_id'] = user.id
            session['first_name'] = user.first_name
            session['last_name'] = user.last_name
            session['pin'] = user.pin
            session['role'] = user.role
            return redirect(url_for('dashboard'))
    elif user_type == '2':  # If user is a guest
        pin = request.form.get('pin')
        role = 'guest'
        first_name = request.form['first_name']
        last_name = request.form['last_name']
        company_id = request.form.get('company_id', None)
        ip_address = request.form['ip_address']

        user = User(role=role, first_name=first_name, last_name=last_name,
                    pin=pin, company_id=company_id, ip_address=ip_address)

        db.session.add(user)
        db.session.commit()

        session['user_id'] = user.id
        session['first_name'] = user.first_name
        session['last_name'] = user.last_name
        session['pin'] = user.pin
        return redirect(url_for('dashboard'))
    else:
        flash("Invalid user type selected!", "danger")


@app.route("/")
def dashboard():
    settings = VotingSettings.query.order_by(desc(VotingSettings.id)).first()

    if not settings:
        return "Voting settings not found", 404

    voting_start = settings.voting_start
    voting_end = settings.voting_end

    current_time = datetime.now()

    if current_time < voting_start or current_time > voting_end:
        return redirect(url_for('not_allowed'))


    # check to ensure user came in with stored session variables, only occurs from login/signup guest page
    if 'user_id' in session:
        user_id = session['user_id']
        first_name = session['first_name']
        last_name = session['last_name']
        pin = session['pin']

        ## REDUNDANT
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
            ## this is all redundant code, we do the exact same thing in our admin page. This is a quick fix.

            # this checks to see if a user has voted for the team as we iterate through each one
            # if they haven't has_voted in our dictionary will be False, otherwise True.
            # This value is used to determine whether the logged in user has the ability to vote for
            # the given team
            user_vote = Vote.query.filter_by(user_id=user_id, team_id=t.id).first()
            has_voted = True if user_vote else False

            team_data[division][t.name] = {
                "division": t.division,
                "members": User.query.filter_by(team_id=t.id).all(),
                "has_voted": has_voted
            }

        return render_template('dashboard.html', first_name=first_name, last_name=last_name, company_pin=pin, companies=company_data, teams=team_data)

    # throws user back to create page if session variables are not found
    flash('You must be logged in to view the dashboard.', 'warning')
    return redirect('/create')


@app.route('/delete_data', methods=['POST'])
def delete_data():
    # because of foreign keys I set up earlier, you have to delete the tables in this order
    if 'delete_votes' in request.form:
        db.session.query(Vote).delete()

    if 'delete_users' in request.form:
        db.session.query(User).delete()

    if 'delete_teams' in request.form:
        db.session.query(Team).delete()

    if 'delete_companies' in request.form:
        db.session.query(Company).delete()

    db.session.commit()

    return redirect(url_for('admin_panel'))


@app.route('/not_allowed')
def not_allowed():
    return "Voting is not allowed at this time."

# this app route, while dirty handles adding team, companies, team members and company members.
# the ability to remove teams, companies, company members and team members is also riddled in this
# this does deserve some clean up, but my brain is seriously fried while rushing this.. and it's 1am
@app.route('/submit', methods=['POST'])
def handle_form():
    action = request.form.get('action')
    entity_type = request.form.get('type')

    if entity_type == 'company':
        if action == 'add':
            name = request.form['company_name']
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
            first_name = request.form['first_name']
            last_name = request.form['last_name']
            pin = request.form['pin']
            company = Company.query.filter_by(name=request.form['company']).first()

            if company:
                new_user = User(
                    role='company',
                    first_name=first_name,
                    last_name=last_name,
                    pin=pin,
                    company_id=company.id,
                    ip_address=request.remote_addr
                )
                db.session.add(new_user)
                db.session.commit()
                flash(f"Member '{first_name} {last_name}' added to '{company.name}'!", "success")
            else:
                flash("Company not found!", "danger")

        elif action == 'remove_member':
            user = User.query.filter_by(
                first_name=request.form['member_name'].split()[0],
                last_name=request.form['member_name'].split()[1]
            ).first()
            if user:
                db.session.delete(user)
                db.session.commit()
                flash(f"Member '{user.first_name} {user.last_name}' removed!", "success")
            else:
                flash("Member not found!", "danger")

    elif entity_type == 'team':
        if action == 'add':
            name = request.form['team_name']
            division = request.form['division']
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
            first_name = request.form['first_name']
            last_name = request.form['last_name']
            pin = generate_pin()
            role = request.form['role']  # 'member' or 'coach'
            team = Team.query.filter_by(name=request.form['team_name']).first()

            if team:
                new_user = User(
                    role=role,
                    first_name=first_name,
                    last_name=last_name,
                    pin=pin,
                    team_id=team.id,
                    ip_address=request.remote_addr
                )
                db.session.add(new_user)
                db.session.commit()
                flash(f"{role.capitalize()} '{first_name} {last_name}' added to '{team.name}'!", "success")
            else:
                flash("Team not found!", "danger")
        elif action == 'remove_member':
            user = User.query.filter_by(
                first_name=request.form['member_name'].split()[0],
                last_name=request.form['member_name'].split()[1],
                team_id=Team.query.filter_by(name=request.form['team_name']).first().id
            ).first()

            if user:
                db.session.delete(user)
                db.session.commit()
                flash(f"Member '{user.first_name} {user.last_name}' removed from '{request.form['team_name']}'!",
                      "success")
            else:
                flash("Member not found in the team!", "danger")

    return redirect('/')


if __name__ == '__main__':
    # Creates tables only if they do not exist
    # this is needed for render as running the commands in a terminal in near non-existent
    # Once the project has been initialized on render, this code is useless unless the database were to get deleted
    with app.app_context():
        db.create_all()

    app.run(debug=True)

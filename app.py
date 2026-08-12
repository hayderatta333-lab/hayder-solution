import sqlite3
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, g, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

DB_PATH = os.path.join(os.path.dirname(__file__), 'crm.db')
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-secret-key-in-production')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB upload limit

# SMTP settings for email integration (set as environment variables on your host)
SMTP_HOST = os.environ.get('SMTP_HOST', '')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASS = os.environ.get('SMTP_PASS', '')


# ---------- Database helpers ----------

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    fresh = not os.path.exists(DB_PATH)
    db = sqlite3.connect(DB_PATH)
    with open(os.path.join(os.path.dirname(__file__), 'schema.sql')) as f:
        db.executescript(f.read())
    # seed default users (one per role, for testing permissions)
    db.execute(
        'INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)',
        ('Admin', 'admin@hayder.com', generate_password_hash('admin123'), 'admin')
    )
    db.execute(
        'INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)',
        ('Sales Manager', 'manager@hayder.com', generate_password_hash('manager123'), 'manager')
    )
    db.execute(
        'INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)',
        ('Sales Rep', 'rep@hayder.com', generate_password_hash('rep123'), 'sales_rep')
    )
    db.commit()
    db.close()
    return fresh


# ---------- Auth ----------

def login_required(f):
@wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def roles_required(*roles):
    def decorator(f):
@wraps(f)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if session.get('user_role') not in roles:
                flash('You do not have permission to access that page.', 'error')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return wrapper
    return decorator


def owner_scope():
    """Sales reps only see records they own; admin/manager see everything."""
    if session.get('user_role') == 'sales_rep':
        return session.get('user_id')
    return None


@app.context_processor
def inject_user():
    reminder_count = 0
    if 'user_id' in session:
        db = get_db()
        now = datetime.now()
        soon = now + timedelta(hours=24)
        row = db.execute(''' SELECT COUNT(*) c FROM activities WHERE completed = 0 AND due_date IS NOT NULL AND due_date <= ? ''', (soon,)).fetchone()
        reminder_count = row['c']
    return dict(current_user=session.get('user_name'),
                current_role=session.get('user_role'),
                reminder_count=reminder_count)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_role'] = user['role']
            return redirect(url_for('dashboard'))
        flash('Invalid email or password', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------- Dashboard ----------

@app.route('/')
@login_required
def dashboard():
    db = get_db()
    total_contacts = db.execute('SELECT COUNT(*) c FROM contacts').fetchone()['c']
    open_deals = db.execute("SELECT COUNT(*) c FROM deals WHERE status='open'").fetchone()['c']
    pipeline_value = db.execute("SELECT COALESCE(SUM(value),0) v FROM deals WHERE status='open'").fetchone()['v']
    won_deals = db.execute("SELECT COUNT(*) c FROM deals WHERE status='won'").fetchone()['c']
    won_value = db.execute("SELECT COALESCE(SUM(value),0) v FROM deals WHERE status='won'").fetchone()['v']

    recent_activities = db.execute(''' SELECT a.*, c.first_name, c.last_name FROM activities a LEFT JOIN contacts c ON a.contact_id = c.id ORDER BY a.created_at DESC LIMIT 6 ''').fetchall()

    upcoming_tasks = db.execute(''' SELECT a.*, c.first_name, c.last_name FROM activities a LEFT JOIN contacts c ON a.contact_id = c.id WHERE a.completed = 0 AND a.due_date IS NOT NULL ORDER BY a.due_date ASC LIMIT 6 ''').fetchall()

    deals_by_stage = db.execute(''' SELECT ps.name stage, ps.color, COUNT(d.id) cnt, COALESCE(SUM(d.value),0) val FROM pipeline_stages ps LEFT JOIN deals d ON d.stage_id = ps.id AND d.status='open' GROUP BY ps.id ORDER BY ps.position ''').fetchall()

    return render_template('dashboard.html',
                            total_contacts=total_contacts,
                            open_deals=open_deals,
                            pipeline_value=pipeline_value,
                            won_deals=won_deals,
                            won_value=won_value,
                            recent_activities=recent_activities,
                            upcoming_tasks=upcoming_tasks,
                            deals_by_stage=deals_by_stage)


# ---------- Contacts ----------

@app.route('/contacts')
@login_required
def contacts():
    db = get_db()
    search = request.args.get('q', '').strip()
    query = ''' SELECT c.*, co.name company_name FROM contacts c LEFT JOIN companies co ON c.company_id = co.id WHERE 1=1 '''
    params = []
    owner = owner_scope()
    if owner:
        query += ' AND c.owner_id = ?'
        params.append(owner)
    if search:
        query += ' AND (c.first_name LIKE ? OR c.last_name LIKE ? OR c.email LIKE ? OR co.name LIKE ?)'
        like = f'%{search}%'
        params += [like, like, like, like]
    query += ' ORDER BY c.created_at DESC'
    rows = db.execute(query, params).fetchall()
    return render_template('contacts.html', contacts=rows, search=search)


@app.route('/contacts/new', methods=['GET', 'POST'])
@login_required
def contact_new():
    db = get_db()
    if request.method == 'POST':
        company_id = request.form.get('company_id') or None
        if not company_id and request.form.get('company_name'):
            cur = db.execute('INSERT INTO companies (name) VALUES (?)', (request.form['company_name'],))
            company_id = cur.lastrowid
        db.execute(''' INSERT INTO contacts (first_name, last_name, email, phone, company_id, job_title, owner_id, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ''', (request.form['first_name'], request.form.get('last_name', ''),
              request.form.get('email', ''), request.form.get('phone', ''),
              company_id, request.form.get('job_title', ''),
              session['user_id'], request.form.get('notes', '')))
        db.commit()
        flash('Contact created', 'success')
        return redirect(url_for('contacts'))
    companies = db.execute('SELECT * FROM companies ORDER BY name').fetchall()
    return render_template('contact_form.html', companies=companies, contact=None)


@app.route('/contacts/<int:contact_id>')
@login_required
def contact_detail(contact_id):
    db = get_db()
    contact = db.execute(''' SELECT c.*, co.name company_name FROM contacts c LEFT JOIN companies co ON c.company_id = co.id WHERE c.id = ? ''', (contact_id,)).fetchone()
    if not contact:
        flash('Contact not found', 'error')
        return redirect(url_for('contacts'))
    deals = db.execute('SELECT * FROM deals WHERE contact_id = ?', (contact_id,)).fetchall()
    activities = db.execute('SELECT * FROM activities WHERE contact_id = ? ORDER BY created_at DESC', (contact_id,)).fetchall()
    attachments_rows = db.execute('SELECT * FROM attachments WHERE related_type=? AND related_id=?', ('contact', contact_id)).fetchall()
    return render_template('contact_detail.html', contact=contact, deals=deals, activities=activities, attachments=attachments_rows)


@app.route('/contacts/<int:contact_id>/delete', methods=['POST'])
@login_required
def contact_delete(contact_id):
    db = get_db()
    db.execute('DELETE FROM contacts WHERE id = ?', (contact_id,))
    db.commit()
    flash('Contact deleted', 'success')
    return redirect(url_for('contacts'))


# ---------- Deals / Pipeline (Kanban) ----------

@app.route('/deals')
@login_required
def deals():
    db = get_db()
    stages = db.execute('SELECT * FROM pipeline_stages ORDER BY position').fetchall()
    query = ''' SELECT d.*, c.first_name, c.last_name, co.name company_name FROM deals d LEFT JOIN contacts c ON d.contact_id = c.id LEFT JOIN companies co ON d.company_id = co.id WHERE d.status = 'open' '''
    params = []
    owner = owner_scope()
    if owner:
        query += ' AND d.owner_id = ?'
        params.append(owner)
    query += ' ORDER BY d.created_at DESC'
    deals_rows = db.execute(query, params).fetchall()
    board = {s['id']: [] for s in stages}
    for d in deals_rows:
        board.setdefault(d['stage_id'], []).append(d)
    contacts_list = db.execute('SELECT id, first_name, last_name FROM contacts ORDER BY first_name').fetchall()
    return render_template('deals.html', stages=stages, board=board, contacts=contacts_list)


@app.route('/deals/new', methods=['POST'])
@login_required
def deal_new():
    db = get_db()
    first_stage = db.execute('SELECT id FROM pipeline_stages ORDER BY position LIMIT 1').fetchone()
    db.execute(''' INSERT INTO deals (title, contact_id, owner_id, value, stage_id, expected_close_date) VALUES (?, ?, ?, ?, ?, ?) ''', (request.form['title'], request.form.get('contact_id') or None,
          session['user_id'], request.form.get('value', 0) or 0,
          first_stage['id'], request.form.get('expected_close_date') or None))
    db.commit()
    flash('Deal created', 'success')
    return redirect(url_for('deals'))


@app.route('/api/deals/<int:deal_id>/move', methods=['POST'])
@login_required
def deal_move(deal_id):
    data = request.get_json()
    stage_id = data.get('stage_id')
    db = get_db()
    stage = db.execute('SELECT * FROM pipeline_stages WHERE id = ?', (stage_id,)).fetchone()
    status = 'open'
    if stage and stage['name'].lower() == 'won':
        status = 'won'
    elif stage and stage['name'].lower() == 'lost':
        status = 'lost'
    db.execute('UPDATE deals SET stage_id = ?, status = ?, updated_at = ? WHERE id = ?',
               (stage_id, status, datetime.now(), deal_id))
    db.commit()
    return jsonify({'success': True})


@app.route('/deals/<int:deal_id>')
@login_required
def deal_detail(deal_id):
    db = get_db()
    deal = db.execute(''' SELECT d.*, c.first_name, c.last_name, co.name company_name FROM deals d LEFT JOIN contacts c ON d.contact_id = c.id LEFT JOIN companies co ON d.company_id = co.id WHERE d.id = ? ''', (deal_id,)).fetchone()
    if not deal:
        flash('Deal not found', 'error')
        return redirect(url_for('deals'))
    attachments_rows = db.execute('SELECT * FROM attachments WHERE related_type=? AND related_id=?', ('deal', deal_id)).fetchall()
    activities_rows = db.execute('SELECT * FROM activities WHERE deal_id = ? ORDER BY created_at DESC', (deal_id,)).fetchall()
    return render_template('deal_detail.html', deal=deal, attachments=attachments_rows, activities=activities_rows)


@app.route('/deals/<int:deal_id>/delete', methods=['POST'])
@login_required
def deal_delete(deal_id):
    db = get_db()
    db.execute('DELETE FROM deals WHERE id = ?', (deal_id,))
    db.commit()
    flash('Deal deleted', 'success')
    return redirect(url_for('deals'))


# ---------- Activities ----------

@app.route('/activities')
@login_required
def activities():
    db = get_db()
    query = ''' SELECT a.*, c.first_name, c.last_name FROM activities a LEFT JOIN contacts c ON a.contact_id = c.id WHERE 1=1 '''
    params = []
    owner = owner_scope()
    if owner:
        query += ' AND a.owner_id = ?'
        params.append(owner)
    query += ' ORDER BY a.completed ASC, a.due_date ASC, a.created_at DESC'
    rows = db.execute(query, params).fetchall()
    contacts_list = db.execute('SELECT id, first_name, last_name FROM contacts ORDER BY first_name').fetchall()
    deals_list = db.execute('SELECT id, title FROM deals ORDER BY title').fetchall()
    return render_template('activities.html', activities=rows, contacts=contacts_list, deals=deals_list)


@app.route('/activities/new', methods=['POST'])
@login_required
def activity_new():
    db = get_db()
    db.execute(''' INSERT INTO activities (type, subject, description, due_date, contact_id, deal_id, owner_id) VALUES (?, ?, ?, ?, ?, ?, ?) ''', (request.form['type'], request.form['subject'], request.form.get('description', ''),
          request.form.get('due_date') or None, request.form.get('contact_id') or None,
          request.form.get('deal_id') or None, session['user_id']))
    db.commit()
    flash('Activity added', 'success')
    return redirect(url_for('activities'))


@app.route('/activities/<int:activity_id>/toggle', methods=['POST'])
@login_required
def activity_toggle(activity_id):
    db = get_db()
    db.execute('UPDATE activities SET completed = NOT completed WHERE id = ?', (activity_id,))
    db.commit()
    return redirect(request.referrer or url_for('activities'))


@app.route('/activities/<int:activity_id>/delete', methods=['POST'])
@login_required
def activity_delete(activity_id):
    db = get_db()
    db.execute('DELETE FROM activities WHERE id = ?', (activity_id,))
    db.commit()
    flash('Activity deleted', 'success')
    return redirect(url_for('activities'))


# ---------- Companies / Accounts ----------

@app.route('/companies')
@login_required
def companies():
    db = get_db()
    search = request.args.get('q', '').strip()
    query = ''' SELECT co.*, (SELECT COUNT(*) FROM contacts c WHERE c.company_id = co.id) contact_count, (SELECT COUNT(*) FROM deals d WHERE d.company_id = co.id) deal_count FROM companies co WHERE 1=1 '''
    params = []
    if search:
        query += ' AND co.name LIKE ?'
        params.append(f'%{search}%')
    query += ' ORDER BY co.name'
    rows = db.execute(query, params).fetchall()
    return render_template('companies.html', companies=rows, search=search)


@app.route('/companies/new', methods=['GET', 'POST'])
@login_required
def company_new():
    db = get_db()
    if request.method == 'POST':
        db.execute(''' INSERT INTO companies (name, industry, website, phone, address) VALUES (?, ?, ?, ?, ?) ''', (request.form['name'], request.form.get('industry', ''),
              request.form.get('website', ''), request.form.get('phone', ''),
              request.form.get('address', '')))
        db.commit()
        flash('Company created', 'success')
        return redirect(url_for('companies'))
    return render_template('company_form.html', company=None)


@app.route('/companies/<int:company_id>')
@login_required
def company_detail(company_id):
    db = get_db()
    company = db.execute('SELECT * FROM companies WHERE id = ?', (company_id,)).fetchone()
    if not company:
        flash('Company not found', 'error')
        return redirect(url_for('companies'))
    contacts_rows = db.execute('SELECT * FROM contacts WHERE company_id = ?', (company_id,)).fetchall()
    deals_rows = db.execute('SELECT * FROM deals WHERE company_id = ?', (company_id,)).fetchall()
    return render_template('company_detail.html', company=company, contacts=contacts_rows, deals=deals_rows)


@app.route('/companies/<int:company_id>/delete', methods=['POST'])
@login_required
def company_delete(company_id):
    db = get_db()
    db.execute('DELETE FROM companies WHERE id = ?', (company_id,))
    db.commit()
    flash('Company deleted', 'success')
    return redirect(url_for('companies'))


# ---------- Users (admin only) ----------

@app.route('/users')
@roles_required('admin')
def users():
    db = get_db()
    rows = db.execute('SELECT id, name, email, role, created_at FROM users ORDER BY created_at').fetchall()
    return render_template('users.html', users=rows)


@app.route('/users/new', methods=['GET', 'POST'])
@roles_required('admin')
def user_new():
    db = get_db()
    if request.method == 'POST':
        existing = db.execute('SELECT id FROM users WHERE email = ?', (request.form['email'].lower(),)).fetchone()
        if existing:
            flash('A user with that email already exists', 'error')
            return render_template('user_form.html', user=None)
        db.execute(''' INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?) ''', (request.form['name'], request.form['email'].strip().lower(),
              generate_password_hash(request.form['password']), request.form['role']))
        db.commit()
        flash('User created', 'success')
        return redirect(url_for('users'))
    return render_template('user_form.html', user=None)


@app.route('/users/<int:user_id>/delete', methods=['POST'])
@roles_required('admin')
def user_delete(user_id):
    if user_id == session['user_id']:
        flash("You can't delete your own account while logged in.", 'error')
        return redirect(url_for('users'))
    db = get_db()
    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    flash('User deleted', 'success')
    return redirect(url_for('users'))


# ---------- Reminders / Notifications ----------

@app.route('/reminders')
@login_required
def reminders():
    db = get_db()
    now = datetime.now()
    soon = now + timedelta(hours=24)
    query = ''' SELECT a.*, c.first_name, c.last_name FROM activities a LEFT JOIN contacts c ON a.contact_id = c.id WHERE a.completed = 0 AND a.due_date IS NOT NULL AND a.due_date <= ? '''
    params = [soon]
    owner = owner_scope()
    if owner:
        query += ' AND a.owner_id = ?'
        params.append(owner)
    query += ' ORDER BY a.due_date ASC'
    rows = db.execute(query, params).fetchall()
    return render_template('reminders.html', reminders=rows, now=now)


# ---------- File Attachments ----------

@app.route('/attachments/upload', methods=['POST'])
@login_required
def attachment_upload():
    related_type = request.form['related_type']   # 'contact' or 'deal'
    related_id = request.form['related_id']
    file = request.files.get('file')
    if file and file.filename:
        original = secure_filename(file.filename)
        stored = f"{related_type}_{related_id}_{int(datetime.now().timestamp())}_{original}"
        file.save(os.path.join(UPLOAD_DIR, stored))
        db = get_db()
        db.execute(''' INSERT INTO attachments (filename, stored_name, related_type, related_id, uploaded_by) VALUES (?, ?, ?, ?, ?) ''', (original, stored, related_type, related_id, session['user_id']))
        db.commit()
        flash('File uploaded', 'success')
    else:
        flash('No file selected', 'error')
    if related_type == 'contact':
        return redirect(url_for('contact_detail', contact_id=related_id))
    return redirect(url_for('deals'))


@app.route('/attachments/<int:attachment_id>/download')
@login_required
def attachment_download(attachment_id):
    db = get_db()
    a = db.execute('SELECT * FROM attachments WHERE id = ?', (attachment_id,)).fetchone()
    if not a:
        flash('File not found', 'error')
        return redirect(url_for('dashboard'))
    return send_from_directory(UPLOAD_DIR, a['stored_name'], as_attachment=True, download_name=a['filename'])


@app.route('/attachments/<int:attachment_id>/delete', methods=['POST'])
@login_required
def attachment_delete(attachment_id):
    db = get_db()
    a = db.execute('SELECT * FROM attachments WHERE id = ?', (attachment_id,)).fetchone()
    if a:
        try:
            os.remove(os.path.join(UPLOAD_DIR, a['stored_name']))
        except OSError:
            pass
        db.execute('DELETE FROM attachments WHERE id = ?', (attachment_id,))
        db.commit()
        flash('Attachment deleted', 'success')
    related_type = request.form.get('related_type')
    related_id = request.form.get('related_id')
    if related_type == 'contact':
        return redirect(url_for('contact_detail', contact_id=related_id))
    return redirect(url_for('deals'))


# ---------- Email Integration ----------

@app.route('/contacts/<int:contact_id>/email', methods=['POST'])
@login_required
def contact_send_email(contact_id):
    db = get_db()
    contact = db.execute('SELECT * FROM contacts WHERE id = ?', (contact_id,)).fetchone()
    if not contact or not contact['email']:
        flash('This contact has no email address on file.', 'error')
        return redirect(url_for('contact_detail', contact_id=contact_id))

    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        flash('Email is not configured yet. Set SMTP_HOST, SMTP_USER and SMTP_PASS on your server first.', 'error')
        return redirect(url_for('contact_detail', contact_id=contact_id))

    subject = request.form.get('subject', '(no subject)')
    body = request.form.get('body', '')
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = SMTP_USER
        msg['To'] = contact['email']
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [contact['email']], msg.as_string())

        db.execute(''' INSERT INTO activities (type, subject, description, contact_id, owner_id, completed) VALUES ('email', ?, ?, ?, ?, 1) ''', (subject, body, contact_id, session['user_id']))
        db.commit()
        flash('Email sent and logged to activity history.', 'success')
    except Exception as e:
        flash(f'Could not send email: {e}', 'error')
    return redirect(url_for('contact_detail', contact_id=contact_id))


# ---------- Reports ----------

@app.route('/reports')
@login_required
def reports():
    db = get_db()
    by_stage = db.execute(''' SELECT ps.name stage, COUNT(d.id) cnt, COALESCE(SUM(d.value),0) val FROM pipeline_stages ps LEFT JOIN deals d ON d.stage_id = ps.id GROUP BY ps.id ORDER BY ps.position ''').fetchall()

    by_owner = db.execute(''' SELECT u.name owner, COUNT(d.id) cnt, COALESCE(SUM(CASE WHEN d.status='won' THEN d.value ELSE 0 END),0) won_val FROM users u LEFT JOIN deals d ON d.owner_id = u.id GROUP BY u.id ''').fetchall()

    monthly = db.execute(''' SELECT strftime('%Y-%m', created_at) month, COUNT(*) cnt, COALESCE(SUM(value),0) val FROM deals WHERE status='won' GROUP BY month ORDER BY month DESC LIMIT 6 ''').fetchall()

    win_rate_row = db.execute(''' SELECT SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) won, SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END) lost FROM deals ''').fetchone()

    total_closed = (win_rate_row['won'] or 0) + (win_rate_row['lost'] or 0)
    win_rate = round((win_rate_row['won'] or 0) / total_closed * 100, 1) if total_closed else 0

    return render_template('reports.html', by_stage=by_stage, by_owner=by_owner,
                            monthly=monthly, win_rate=win_rate,
                            won=win_rate_row['won'] or 0, lost=win_rate_row['lost'] or 0)


if __name__ == '__main__':
    if not os.path.exists(DB_PATH):
        init_db()
        print('Database initialized with default admin user: admin@hayder.com / admin123')
    app.run(debug=True, host='0.0.0.0', port=5000)

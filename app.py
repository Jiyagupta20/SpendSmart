import csv
import io
import logging
from logging.handlers import RotatingFileHandler
import os
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, make_response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, date as py_date
from sqlalchemy import func
from flask_migrate import Migrate
from dotenv import load_dotenv

# Local imports
from database import db, User, Expense, RecurringExpense

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default-dev-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///spendsmart.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- LOGGING SETUP ---
if not os.path.exists('logs'):
    os.mkdir('logs')
file_handler = RotatingFileHandler('logs/spendsmart.log', maxBytes=10240, backupCount=10)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)
app.logger.info('SpendSmart startup')

# Initialize DB, Migrate and Login Manager
db.init_app(app)
migrate = Migrate(app, db)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

# Register API Blueprint
from api_routes import api_bp
app.register_blueprint(api_bp)

# --- CONFIGURATION ---
CATEGORIES = ["Food", "Travel", "Shopping", "Bills", "Entertainment", "Health", "Other"]

# Color mapping for charts and UI
CATEGORY_COLORS = {
    "Food": "#FF6384",          # Pinkish Red
    "Travel": "#36A2EB",        # Blue
    "Shopping": "#FFCE56",      # Yellow
    "Bills": "#4BC0C0",         # Teal
    "Entertainment": "#9966FF", # Purple
    "Health": "#FF9F40",        # Orange
    "Other": "#C9CBCF"          # Grey
}

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def process_recurring_expenses(user_id):
    """Checks for due recurring expenses and generates single Expense entries"""
    today = py_date.today()
    recurring_profiles = RecurringExpense.query.filter_by(user_id=user_id, is_active=True).all()
    
    new_expenses_count = 0
    for profile in recurring_profiles:
        # Determine the starting point for generation
        current_date = profile.last_generated_date if profile.last_generated_date else profile.start_date
        
        while True:
            # Calculate next due date
            if profile.frequency == 'Daily':
                next_date = current_date + timedelta(days=1)
            elif profile.frequency == 'Weekly':
                next_date = current_date + timedelta(weeks=1)
            elif profile.frequency == 'Monthly':
                # Move to the same day next month
                month = current_date.month
                year = current_date.year + (month // 12)
                month = (month % 12) + 1
                try:
                    next_date = current_date.replace(year=year, month=month)
                except ValueError:
                    # Handle cases like Jan 31st -> Feb (non-existent 30th/31st)
                    # For simplicity, move to the last day of the next month
                    if month == 12:
                        next_date = py_date(year+1, 1, 1) - timedelta(days=1)
                    else:
                        next_date = py_date(year, month + 1, 1) - timedelta(days=1)
            else:
                break

            if next_date > today:
                break
                
            # Create the expense record
            new_expense = Expense(
                user_id=user_id,
                amount=profile.amount,
                category=profile.category,
                date=next_date,
                note=f"[Recurring] {profile.note}" if profile.note else "[Recurring]"
            )
            db.session.add(new_expense)
            profile.last_generated_date = next_date
            current_date = next_date
            new_expenses_count += 1
            
    if new_expenses_count > 0:
        db.session.commit()
    return new_expenses_count

# --- ROUTES ---

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email').strip().lower()
        password = request.form.get('password')
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
            return redirect(url_for('register'))
            
        hashed_pw = generate_password_hash(password, method='scrypt')
        new_user = User(username=username, email=email, password_hash=hashed_pw)
        db.session.add(new_user)
        db.session.commit()
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
        
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email').strip().lower()
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        
        if user:
            if check_password_hash(user.password_hash, password):
                login_user(user)
                app.logger.info(f'User {email} logged in successfully')
                return redirect(url_for('dashboard'))
            else:
                app.logger.warning(f'Failed login attempt for {email}: Incorrect password')
                flash('Incorrect password. Please try again.', 'danger')
        else:
            app.logger.warning(f'Failed login attempt for {email}: User not found')
            flash(f'No account found with email: {email}', 'danger')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Calculate Monthly Spending
    today = datetime.now()
    first_of_month = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_total = db.session.query(func.sum(Expense.amount)).filter(
        Expense.user_id == current_user.id,
        Expense.date >= first_of_month.date()
    ).scalar() or 0.0
    
    # Calculate Weekly Spending
    start_of_week = today - timedelta(days=today.weekday())
    weekly_total = db.session.query(func.sum(Expense.amount)).filter(
        Expense.user_id == current_user.id,
        Expense.date >= start_of_week.date()
    ).scalar() or 0.0
    
    # Category Comparison (for Pie Chart)
    category_data = db.session.query(
        Expense.category, func.sum(Expense.amount)
    ).filter(
        Expense.user_id == current_user.id,
        Expense.date >= first_of_month.date()
    ).group_by(Expense.category).all()
    
    chart_labels = [row[0] for row in category_data]
    chart_values = [row[1] for row in category_data]
    chart_colors = [CATEGORY_COLORS.get(row[0], "#000000") for row in category_data]
    
    # Daily Spending (for Bar Chart)
    daily_data = db.session.query(
        Expense.date, func.sum(Expense.amount)
    ).filter(
        Expense.user_id == current_user.id,
        Expense.date >= first_of_month.date()
    ).group_by(Expense.date).order_by(Expense.date).all()
    
    daily_labels = [row[0].strftime('%d %b') for row in daily_data]
    daily_values = [row[1] for row in daily_data]
    
    # Budget Logic
    budget_left = current_user.monthly_budget - monthly_total
    is_over_budget = monthly_total > current_user.monthly_budget if current_user.monthly_budget > 0 else False
    
    return render_template('dashboard.html', 
                           monthly_total=monthly_total,
                           weekly_total=weekly_total,
                           budget_left=budget_left,
                           is_over_budget=is_over_budget,
                           chart_labels=chart_labels,
                           chart_values=chart_values,
                           chart_colors=chart_colors,
                           daily_labels=daily_labels,
                           daily_values=daily_values,
                           categories=CATEGORIES)

@app.route('/add', methods=['GET', 'POST'])
@login_required
def add_expense():
    if request.method == 'POST':
        amount = float(request.form.get('amount'))
        category = request.form.get('category')
        date_str = request.form.get('date')
        note = request.form.get('note')
        is_recurring = request.form.get('is_recurring') == 'on'
        frequency = request.form.get('frequency')
        
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        if is_recurring:
            new_recurring = RecurringExpense(
                user_id=current_user.id,
                amount=amount,
                category=category,
                frequency=frequency,
                start_date=date_obj,
                note=note
            )
            db.session.add(new_recurring)
            db.session.commit()
            flash('Recurring expense pattern saved!', 'success')
            return redirect(url_for('dashboard'))
        else:
            new_expense = Expense(
                user_id=current_user.id,
                amount=amount,
                category=category,
                date=date_obj,
                note=note
            )
            db.session.add(new_expense)
            db.session.commit()
            flash('Expense added successfully!', 'success')
            return redirect(url_for('dashboard'))
        
    return render_template('add_expense.html', categories=CATEGORIES, today=datetime.now().strftime('%Y-%m-%d'))

@app.route('/expenses')
@login_required
def expenses():
    user_expenses = Expense.query.filter_by(user_id=current_user.id).order_by(Expense.date.desc()).all()
    return render_template('expenses.html', expenses=user_expenses, categories=CATEGORIES, colors=CATEGORY_COLORS)

@app.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_expense(id):
    expense = Expense.query.get_or_404(id)
    if expense.user_id != current_user.id:
        return redirect(url_for('expenses'))
        
    if request.method == 'POST':
        expense.amount = float(request.form.get('amount'))
        expense.category = request.form.get('category')
        expense.date = datetime.strptime(request.form.get('date'), '%Y-%m-%d').date()
        expense.note = request.form.get('note')
        
        db.session.commit()
        flash('Expense updated successfully!', 'success')
        return redirect(url_for('expenses'))
        
    return render_template('edit_expense.html', expense=expense, categories=CATEGORIES)

@app.route('/delete/<int:id>')
@login_required
def delete_expense(id):
    expense = Expense.query.get_or_404(id)
    if expense.user_id == current_user.id:
        db.session.delete(expense)
        db.session.commit()
        flash('Expense deleted successfully!', 'success')
    return redirect(url_for('expenses'))

@app.route('/budget', methods=['GET', 'POST'])
@login_required
def budget():
    if request.method == 'POST':
        new_budget = float(request.form.get('budget'))
        current_user.monthly_budget = new_budget
        db.session.commit()
        flash('Monthly budget updated!', 'success')
        return redirect(url_for('dashboard'))
        
    return render_template('budget.html')

@app.route('/export/csv')
@login_required
def export_csv():
    """Exports user expenses to a CSV file"""
    user_expenses = Expense.query.filter_by(user_id=current_user.id).order_by(Expense.date.desc()).all()
    
    # Use StringIO to create CSV in memory
    si = io.StringIO()
    cw = csv.writer(si)
    
    # Write header
    cw.writerow(['Date', 'Category', 'Amount', 'Note'])
    
    # Write data
    for exp in user_expenses:
        cw.writerow([exp.date.strftime('%Y-%m-%d'), exp.category, exp.amount, exp.note])
    
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=expenses_export.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route('/recurring')
@login_required
def recurring_list():
    """Lists all active recurring expense profiles"""
    profiles = RecurringExpense.query.filter_by(user_id=current_user.id).all()
    return render_template('recurring.html', profiles=profiles)

@app.route('/recurring/delete/<int:id>')
@login_required
def delete_recurring(id):
    """Deletes a recurring expense profile"""
    profile = RecurringExpense.query.get_or_404(id)
    if profile.user_id == current_user.id:
        db.session.delete(profile)
        db.session.commit()
        flash('Recurring expense deleted.', 'success')
    return redirect(url_for('recurring_list'))

# --- ERROR HANDLERS ---
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

# --- CLI COMMANDS ---
@app.cli.command("process-recurring")
def process_recurring_command():
    """CLI command to process recurring expenses for all users"""
    app.logger.info("Starting background processing for recurring expenses")
    users = User.query.all()
    total_generated = 0
    for user in users:
        count = process_recurring_expenses(user.id)
        if count > 0:
            total_generated += count
            app.logger.info(f"Generated {count} expenses for user_id: {user.id}")
    
    app.logger.info(f"Background processing finished. Total generated: {total_generated}")
    print(f"Processed recurring expenses. Total generated: {total_generated}")

# --- INITIALIZATION ---
if __name__ == '__main__':
    app.run(debug=True, port=5001)

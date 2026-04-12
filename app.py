import os
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from sqlalchemy import func

# Local imports
from database import db, User, Expense

app = Flask(__name__)
app.config['SECRET_KEY'] = 'spendsmart-secret-key-12345'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///spendsmart.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize DB and Login Manager
db.init_app(app)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

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
        email = request.form.get('email')
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
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Login failed. Check your email and password.', 'danger')
            
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
        
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        
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

# --- ERROR HANDLERS ---
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

# --- INITIALIZATION ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5001)

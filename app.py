import anthropic
import csv
import io
import logging
from logging.handlers import RotatingFileHandler
import os
import re
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

# Anthropic API Setup
anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
if not anthropic_key or "your_anthropic_api_key_here" in anthropic_key:
    app.logger.warning("ANTHROPIC_API_KEY is not set. AI features will not work.")
    ai_client = None
else:
    ai_client = anthropic.Anthropic(api_key=anthropic_key)

# --- CONFIGURATION ---
CATEGORIES = ["Food", "Travel", "Extra Expenses", "Bills", "Entertainment", "Health", "Other"]

# Color mapping for charts and UI
CATEGORY_COLORS = {
    "Food": "#FF6384",          # Pinkish Red
    "Travel": "#36A2EB",        # Blue
    "Extra Expenses": "#FFCE56",      # Yellow
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
    return render_template('landing.html')

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

def fallback_ai_parser(text):
    """Simple rule-based parser for when AI service is unavailable"""
    expenses = []
    
    # Common categories and their keywords
    keywords = {
        "Food": ["food", "khana", "khane", "khaya", "lunch", "dinner", "breakfast", "tea", "chai", "coffee", "restaurant", "swiggy", "zomato", "biryani", "pizza", "burger", "drink", "party"],
        "Travel": ["travel", "auto", "taxi", "uber", "ola", "metro", "bus", "petrol", "diesel", "fare", "train", "flight", "rickshaw", "cab", "fuel"],
        "Extra Expenses": ["shopping", "cloth", "amazon", "flipkart", "myntra", "mall", "shoe", "watch", "shirt", "jeans", "bought", "buy", "extra"],
        "Bills": ["bill", "recharge", "electricity", "water", "rent", "wifi", "internet", "phone", "gas", "payment", "paid"],
        "Entertainment": ["movie", "netflix", "hotstar", "game", "party", "club", "concert", "outing", "show", "ticket"],
        "Health": ["health", "medicine", "doctor", "gym", "hospital", "test", "checkup", "tablet", "clinic"],
    }

    # Number word mapping
    number_map = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, 
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "hundred": 100, "hundreds": 100,
        "thousand": 1000, "thousands": 1000,
        "lakh": 100000, "lakhs": 100000
    }
    
    # Split by common conjunctions
    parts = re.split(r'[,.\n]|and|aur|&', text.lower())
    
    today_str = py_date.today().strftime('%Y-%m-%d')
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
            
        # 1. Try to find digits
        amount = 0
        amounts = re.findall(r'(\d+(?:\.\d+)?)', part)
        if amounts:
            amount = float(amounts[0])
            # Check if there's a multiplier following (e.g. "1 thousand")
            for word, val in number_map.items():
                if word in part and val >= 100:
                    if re.search(rf'{amounts[0]}\s*{word}', part):
                        amount *= val
                        break
        else:
            # 2. Try to find number words if no digits
            for word, val in number_map.items():
                if re.search(rf'\b{word}\b', part):
                    amount = val
                    break

        if amount > 0:
            category = "Other"
            
            # Check for keywords to determine category
            for cat, words in keywords.items():
                if any(word in part for word in words):
                    category = cat
                    break
            
            # 3. Simple Translation/Cleaning for the note
            # Find the specific keyword that triggered the category
            found_keyword = ""
            for cat, words in keywords.items():
                for word in words:
                    if re.search(rf'\b{word}\b', part):
                        found_keyword = word
                        break
                if found_keyword: break

            hindi_indicators = ["maine", "aaj", "kharch", "kiye", "pe", "mein", "rupay", "rupaya", "rupaye"]
            is_hindi = any(word in part for word in hindi_indicators)
            
            if is_hindi:
                note = found_keyword.capitalize() if found_keyword else f"{category} expense"
            else:
                # Remove the amount and common noise words to clean up the note
                clean_note = part
                if amounts: clean_note = clean_note.replace(amounts[0], "")
                for word in ["rupees", "rupee", "rs", "in", "on", "for", "spent"]:
                    clean_note = clean_note.replace(word, "")
                note = clean_note.strip().capitalize() or f"{category} expense"
            
            expenses.append({
                "amount": amount,
                "category": category,
                "note": note,
                "date": today_str
            })
            
    return expenses

@app.route('/chat', methods=['POST'])
@login_required
def chat():
    if not ai_client:
        return jsonify({"reply": "AI Chat is currently disabled because the API key is not set. Please add your ANTHROPIC_API_KEY to the .env file."}), 503
    """AI Chatbot for expense management"""
    data = request.get_json()
    user_message = data.get('message', '')
    
    # Fetch recent expenses for context (last 30 days)
    thirty_days_ago = datetime.now() - timedelta(days=30)
    recent_expenses = Expense.query.filter(
        Expense.user_id == current_user.id,
        Expense.date >= thirty_days_ago.date()
    ).order_by(Expense.date.desc()).all()
    
    # Format context for Claude
    expense_context = "\n".join([
        f"- {e.date}: {e.amount} on {e.category} ({e.note})" 
        for e in recent_expenses
    ])
    
    system_prompt = (
        "You are a personal finance assistant for SpendSmart, an expense tracker app. "
        "Help users understand their spending habits, give saving tips, answer questions "
        "about their expenses, and suggest budget improvements. Keep responses short and friendly. "
        "LANGUAGE RULE: The user may type or speak in Hindi or English or a mix of both (Hinglish). "
        "Always understand both languages but always reply in English translation only. "
        "If the user says 'aaj maine 100 rupaye khane pe kharch kiye' treat it the same as 'today I spent 100 rupees on food'."
    )
    
    prompt = f"User's recent expenses (last 30 days):\n{expense_context}\n\nUser: {user_message}"
    
    try:
        response = ai_client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )
        reply = response.content[0].text
        return jsonify({"reply": reply})
    except Exception as e:
        app.logger.error(f"Chat error: {str(e)}")
        return jsonify({"reply": "I'm sorry, I encountered an error. Please try again later."}), 500

@app.route('/parse-voice', methods=['POST'])
@login_required
def parse_voice():
    """Parse spoken expense text into JSON"""
    data = request.get_json()
    spoken_text = data.get('text', '')

    if not ai_client:
        # Fallback to local parser
        app.logger.info(f"Using fallback parser for voice: {spoken_text}")
        expenses = fallback_ai_parser(spoken_text)
        if expenses:
            return jsonify(expenses[0]) # Return first extracted expense
        return jsonify({"amount": 0, "category": "Other", "note": spoken_text, "date": py_date.today().strftime('%Y-%m-%d')})
    
    prompt = (
        "The user may have spoken in Hindi, English, or Hinglish. Understand the language and extract expense details. "
        "Return ONLY a JSON object with these fields: amount (number), category (one of: Food, Travel, Extra Expenses, Bills, Entertainment, Health, Other), "
        "note (string in English translation only), date (today's date in YYYY-MM-DD format). "
        "Always write the note in English translation only regardless of input language.\n"
        f"Text: {spoken_text}"
    )
    
    try:
        response = ai_client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        # Extract JSON from response (handling potential markdown)
        result_text = response.content[0].text.strip()
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()
            
        import json
        parsed_data = json.loads(result_text)
        return jsonify(parsed_data)
    except Exception as e:
        app.logger.error(f"Voice parse error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/daily-log')
@login_required
def daily_log():
    """Render the daily log page"""
    return render_template('daily_log.html', categories=CATEGORIES, today=datetime.now().strftime('%Y-%m-%d'))

@app.route('/process-daily', methods=['POST'])
@login_required
def process_daily():
    """Extract multiple expenses from a bulk text/speech"""
    data = request.get_json()
    text = data.get('text', '')

    if not ai_client:
        # Fallback to local parser
        app.logger.info(f"Using fallback parser for daily log: {text}")
        expenses = fallback_ai_parser(text)
        return jsonify(expenses)
    
    prompt = (
        "The following text is a voice-to-text transcription of a user describing their daily expenses. "
        "The user may have spoken in Hindi, English, or Hinglish (Hindi in Roman script). "
        "The transcription might contain phonetic or symbolic errors. "
        "IMPORTANT: The transcription engine often makes mistakes with numbers. For example: "
        "- 'one ₹50' or '1 ₹50' usually means 'one fifty' (150). "
        "- 'two ₹50' means 'two fifty' (250). "
        "- 'one ₹100' means 'one hundred' (100). "
        "Please use common sense and context to correct these errors. "
        "1. Understand the intended meaning and correct any transcription errors. "
        "2. Extract ALL expenses mentioned. "
        "3. Return ONLY a JSON array of objects. Each object must have: "
        "amount (number), category (one of: Food, Travel, Extra Expenses, Bills, Entertainment, Health, Other), "
        "note (string, ALWAYS in English translation only), date (today's date in YYYY-MM-DD). "
        "No matter what language the input is, always write notes in English translation only.\n\n"
        f"Transcription Text: {text}"
    )
    
    try:
        response = ai_client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        result_text = response.content[0].text.strip()
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()
            
        import json
        expenses_list = json.loads(result_text)
        return jsonify(expenses_list)
    except Exception as e:
        app.logger.error(f"Daily process error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/bulk-save', methods=['POST'])
@login_required
def bulk_save():
    """Save multiple expenses at once"""
    expenses = request.get_json()
    if not isinstance(expenses, list):
        return jsonify({"error": "Invalid data format"}), 400
        
    try:
        for data in expenses:
            new_expense = Expense(
                user_id=current_user.id,
                amount=float(data['amount']),
                category=data['category'],
                date=datetime.strptime(data['date'], '%Y-%m-%d').date(),
                note=data.get('note', '')
            )
            db.session.add(new_expense)
        
        db.session.commit()
        return jsonify({"message": f"{len(expenses)} expenses added successfully!"})
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Bulk save error: {str(e)}")
        return jsonify({"error": str(e)}), 500

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

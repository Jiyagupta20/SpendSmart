from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from database import db, Expense
from datetime import datetime, timedelta
from sqlalchemy import func

api_bp = Blueprint('api', __name__, url_prefix='/api/v1')

@api_bp.route('/expenses', methods=['GET'])
@login_required
def get_expenses():
    """Retrieve filtered list of expenses for the current user"""
    # Optional filtering parameters
    category = request.args.get('category')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    query = Expense.query.filter_by(user_id=current_user.id)

    if category:
        query = query.filter(Expense.category == category)
    if start_date:
        query = query.filter(Expense.date >= datetime.strptime(start_date, '%Y-%m-%d').date())
    if end_date:
        query = query.filter(Expense.date <= datetime.strptime(end_date, '%Y-%m-%d').date())

    expenses = query.order_by(Expense.date.desc()).all()
    
    return jsonify([{
        'id': e.id,
        'amount': e.amount,
        'category': e.category,
        'date': e.date.strftime('%Y-%m-%d'),
        'note': e.note
    } for e in expenses])

@api_bp.route('/expenses', methods=['POST'])
@login_required
def add_expense():
    """Add a new expense via JSON"""
    data = request.get_json()
    if not data or 'amount' not in data or 'category' not in data or 'date' not in data:
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        new_expense = Expense(
            user_id=current_user.id,
            amount=float(data['amount']),
            category=data['category'],
            date=datetime.strptime(data['date'], '%Y-%m-%d').date(),
            note=data.get('note', '')
        )
        db.session.add(new_expense)
        db.session.commit()
        return jsonify({'message': 'Expense added successfully', 'id': new_expense.id}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@api_bp.route('/stats/summary', methods=['GET'])
@login_required
def get_stats():
    """Get weekly and monthly totals for dashboard usage"""
    today = datetime.now()
    first_of_month = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_of_week = today - timedelta(days=today.weekday())

    monthly_total = db.session.query(func.sum(Expense.amount)).filter(
        Expense.user_id == current_user.id,
        Expense.date >= first_of_month.date()
    ).scalar() or 0.0

    weekly_total = db.session.query(func.sum(Expense.amount)).filter(
        Expense.user_id == current_user.id,
        Expense.date >= start_of_week.date()
    ).scalar() or 0.0

    return jsonify({
        'monthly_total': float(monthly_total),
        'weekly_total': float(weekly_total),
        'monthly_budget': current_user.monthly_budget,
        'over_budget': monthly_total > current_user.monthly_budget if current_user.monthly_budget > 0 else False
    })

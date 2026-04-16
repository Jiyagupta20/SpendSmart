# SpendSmart 💰

**SpendSmart** is a sleek, modern Expense Tracker web application built to help you take control of your finances. track your daily spending, set monthly budgets, and visualize your financial habits with beautiful interactive charts.

## ✨ Features

- **Intuitive Dashboard**: At-a-glance view of your monthly and weekly spending.
- **Interactive Visualizations**: Category-wise doughnut charts and daily spending bar charts powered by Chart.js.
- **Budget Monitoring**: Set a monthly budget and receive visual alerts as you approach or exceed your limit.
- **Complete Expense Management**: Easily add, edit, or delete expenses with detailed notes.
- **Smart Filtering**: Instant search and category-based filtering to find any transaction in seconds.
- **Backend Engine**: Powerful automation for recurring expenses and background data processing.
- **RESTful API**: Ready-to-use JSON endpoints for expenses and stats summary.
- **Database Migrations**: Managed schema updates via Flask-Migrate (Alembic).
- **System Logging**: Detailed activity logging for security and debugging.
- **Data Export**: Export your entire expense history to a professional CSV file.

## 🚀 Tech Stack

- **Core**: Python, Flask
- **Database**: SQLite with **Flask-SQLAlchemy** & **Flask-Migrate**
- **Architecture**: RESTful API Blueprint, CLI Background Tasks
- **Configuration**: Environment variables via **python-dotenv**
- **Observability**: **Rotating Logging** system
- **Frontend**: HTML5, Vanilla CSS3, Chart.js

## 🛠️ Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Jiyagupta20/SpendSmart.git
   cd SpendSmart
   ```

2. **Setup virtual environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Configuration**:
   Create a `.env` file in the root directory:
   ```env
   SECRET_KEY=your-secret-key
   DATABASE_URL=sqlite:///spendsmart.db
   FLASK_APP=app.py
   ```

5. **Initialize Database**:
   ```bash
   flask db upgrade
   ```

6. **Run the app**:
   ```bash
   python3 app.py
   ```

## ⚙️ Background Tasks

To process recurring expenses automatically (can be scheduled via Cron):
```bash
flask process-recurring
```

## 📄 License

This project is licensed under the MIT License.

---

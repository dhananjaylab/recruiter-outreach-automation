# Recruiter Outreach Automation

This project automates sending personalized outreach emails to a list of recruiters.

It loads recruiter data from a CSV or PDF file, merges it with an email template, and sends the emails concurrently with rate-limiting and retry logic.

## 🌟 Key Features

* **Flexible Input:** Load recruiters from a clean **CSV file** (recommended) or a **PDF file**.
* **Robust PDF Parsing:** Capable of extracting data from multi-page PDF tables, even with messy or broken rows.
* **Concurrency:** Uses a `ThreadPoolExecutor` to send multiple emails at once, speeding up the process.
* **Rate Limiting:** Includes a `RateLimiter` to avoid being flagged as spam by your email provider.
* **Resilient Sending:** Automatically retries failed email sends with exponential backoff.
* **Secure Configuration:** Uses a `.env` file to store sensitive credentials like your email password.

## ⚙️ Setup & Configuration

### 1. Installation

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/your-username/recruiter-outreach-automation.git](https://github.com/your-username/recruiter-outreach-automation.git)
    cd recruiter-outreach-automation
    ```

2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install the required packages:**
    ```bash
    pip install -r requirements.txt
    ```

### 2. Configuration

1.  **Create a `.env` file** in the root directory. This file stores your sensitive credentials.
    > **Important:** If you use Gmail, you must generate an "App Password" to use here. Your regular password will not work.

    Copy the following into your `.env` file and fill in your details:

    ```ini
    # .env file content

    # --- Sender Email Credentials ---
    EMAIL_USER="your-email@gmail.com"
    EMAIL_PASSWORD="your-google-app-password"
    
    # --- Paths ---
    # Path to your resume PDF
    RESUME_PATH="path/to/Your_Resume.pdf"
    # Path to the email template
    EMAIL_TEMPLATE_PATH="email_template.md"

    # --- SMTP Settings (Defaults are for Gmail) ---
    SMTP_SERVER="smtp.gmail.com"
    SMTP_PORT=587

    # --- Sending & Rate Limit Settings (Optional) ---
    # Max emails to send at once
    MAX_EMAIL_THREADS=10
    # Max retries for a failed email
    MAX_EMAIL_RETRIES=3
    # Max emails (CALLS) per time period (PERIOD) in seconds
    EMAIL_CALLS_PER_PERIOD=10
    EMAIL_PERIOD=60
    ```

2.  **Customize `email_template.md`**:
    Edit the `email_template.md` file. The script will replace `{recruiter_name}` and `{company_name}`.

## 🏃‍♂️ How to Run

You can provide recruiter data using either a CSV file (recommended) or a PDF file.

### Option 1: Run with a CSV file (Recommended)

This is the fastest and most reliable method.

1.  Create a CSV file (e.g., `my_recruiters.csv`) with the following columns: `Name`, `Company`, `Email`.

    *Example `my_recruiters.csv`:*
    ```csv
    Name,Company,Email
    Jane Doe,Google,jane.doe@google.com
    John Smith,Microsoft,john.smith@microsoft.com
    ```

2.  Run the script using the `--csv` flag:
    ```bash
    python src/main.py --csv "my_recruiters.csv"
    ```

### Option 2: Run with a PDF file

This will use the built-in `pdfplumber` logic to extract data. It will also save a `recruiters_list.csv` file as an artifact.

1.  Place your PDF (e.g., `HR_Contacts.pdf`) in the project directory.

2.  Run the script using the `--pdf` flag:
    ```bash
    python src/main.py --pdf "HR_Contacts.pdf"
    ```
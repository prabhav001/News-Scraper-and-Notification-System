# News-Scraper-and-Notification-System
# 🚗 Automotive News Scraper & AI-powered Tagger

The **Automotive News Scraper** is a real-time, automated news aggregation and classification system built in **Python**.  
It continuously **scrapes** automotive news from multiple online sources, processes & cleans the data, stores it in a **MySQL database**, syncs data to **Google Sheets**, sends **notification emails**, and **auto-tags** each article using an **LLM-based NLP classifier**.

---

## ✨ Features

### 🔍 Multi-source News Scraping
Pulls articles from multiple OEMs, automobile brands, and automotive media portals, including:

- **OEMs & Automotive Brands**  
  `Ather Energy`, `BMW`, `MG Motor`, `Lexus`, `Audi`, `Land Rover`, `Volkswagen`, `Skoda`, `Porsche`,  
  `Toyota`, `Citroën`, `Renault`, `Tata Motors`, `Maruti Suzuki`, `Mahindra`, `Hyundai`, `Kia`, `BYD`,  
  `VinFast`, `Xiaomi`, `Force Motors`, `Yamaha`, `Suzuki`, `KTM`, `Bounce`

- **Automotive Media Sites**  
  `91Wheels`, `CarDekho`, `BikeDekho`, etc.

---

### 🔄 Automated ETL Pipeline
**Extract → Transform → Load** automation:
- Scrapes HTML/XML/JSON data from multiple sources
- Cleans and normalizes data
- Inserts into MySQL DB
- Pushes to Google Sheets
- Sends email notifications

---

### 🤖 AI-Powered Tagging
- Uses **Google Generative AI (Gemini models)** for natural language classification.
- Classifies articles into **predefined categories**, e.g.:
  `Corporate`, `Expansion`, `Partnership`, `Upcoming`, `New Launch`,  
  `Price Change`, `Event`, `Milestone`, `Facelift`, `Bookings`, `Spyshots`,  
  `Review`, `Variant Launch`
- Automatic **model fallback** on quota/rate-limit errors.

---

### 📊 Google Sheets Integration
- **Automatically updates** a Google Sheet for easy access.
- Each row contains `Source`, `Title`, `Date`, `URL`, `Tags`.

---

### 📧 Email Notifications
- Sends **HTML-formatted summary emails** for newly fetched articles.
- Includes **source, title, date, and links**.

---

### 🛡 Robust Error Handling & Logging
- Centralized logging with `logging` module.
- Session-specific error reports sent via email.
- All scraping/classification issues tracked in `compscrapers.log`.

---

## 🛠 Tech Stack

**Language:**  
- Python 3

**Libraries:**
- **Web Scraping:** `requests`, `beautifulsoup4`, `cloudscraper`
- **Data Handling:** `json`, `re`, `pandas`
- **Date Processing:** `datetime`, `timedelta`
- **Email:** `smtplib`, `email.mime`
- **APIs:** `gspread` (Google Sheets), `google.generativeai` (LLM tagging)
- **Database:** `mysql.connector` (MySQL backend)
- **Automation:** cron jobs (or any scheduler)
- **Logging:** Python logging module

**Databases & APIs:**
- MySQL (`news_notifications` database)
- Google Sheets API
- Google Generative AI API

---

## ⚙️ System Architecture & Workflow

### 1️⃣ Scraping Layer
- Fetches HTML/XML/JSON from each news source.
- Parses DOM via **BeautifulSoup** or processes JSON API.

### 2️⃣ Transformation Layer
- Extracts:  
  `Title`, `URL`, `Date`, `CompanyName`
- Normalizes date → `YYYY-MM-DD`
- Cleans URLs & text

### 3️⃣ Loading Layer
- Inserts new entries into **MySQL DB** (avoids duplicates)
- Updates Google Sheets
- Sends formatted notification email

### 4️⃣ Classification Layer
- Passes article titles to **LLM-based classifier**
- Assigns relevant tags from automotive categories
- Stores tags in DB & Sheets

### 5️⃣ Error Handling Layer
- Logs all scraping & classification errors to `compscrapers.log`
- Sends session log summary via email

---

## 📊 Example Outputs

**MySQL Table (final)**

| id  | title                              | link                          | date       | CompanyName   | tags (JSON) |
|-----|------------------------------------|-------------------------------|------------|--------------|-------------|
| 101 | Honda Launches City Sport Edition  | https://...                   | 2025-08-10 | Honda        | ["New Launch"] |
| 102 | BMW Opens Factory in Chennai       | https://...                   | 2025-08-11 | BMW          | ["Expansion"] |

**Google Sheets Columns**
Source | Title | Date | URL | Tags


**Email Summary Example**
🚨 5 New Automotive News Alerts

[91Wheels] Honda Launches City Sport Edition — 2025-08-10
[BMW] New Factory in Chennai — 2025-08-11

---

## 🚀 Future Improvements
- **Async scraping** using `aiohttp` for higher performance
- **Multi-language support** for non-English sources
- **AI summarization** of articles before sending emails
- **Duplicate detection** with fuzzy matching
- **Dashboard frontend** for browsing historical data

---

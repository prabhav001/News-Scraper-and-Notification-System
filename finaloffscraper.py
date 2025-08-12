import json
from bs4 import BeautifulSoup
import mysql.connector
import re
import time
import smtplib
import requests
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urljoin
from datetime import datetime
import cloudscraper
import pandas as pd
import logging
import traceback
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import json
from oauth2client.service_account import ServiceAccountCredentials
from datetime import timedelta
import timedelta
PROMPT_TEMPLATE = """
You are an automotive news analyst. Assign ALL relevant tags from this list to the news headline below.
Tags: {labels}

For each headline, output ONLY a valid JSON list of tags, e.g. ["New Launch", "Price Change"].
If none apply, output [].
**Tag Definitions:**
- Corporate: News about company-level strategies, management changes, financial results, or business decisions.
- Expansion: Announcements about new factories, dealerships, service centers, or entering new markets/regions.
- Partnership: News about collaborations, joint ventures, or strategic alliances between companies.
- Upcoming: Information about vehicles, events, or features that are announced but not yet available.
- New Launch: The introduction of a brand new vehicle model to the market.
- Price Change: Announcements about increases or decreases in the price of vehicles or related products.
- Event: Information about auto expos, launches, press conferences, or other industry events.
- Milestone: Achievements such as sales records, production numbers, or anniversaries.
- Facelift: Updates about mid-cycle design refreshes or cosmetic updates to existing vehicle models.
- Bookings: News about the opening or closing of bookings/reservations for vehicles.
- Spyshots: Leaked or unofficial photos of vehicles being tested, often camouflaged.
- Review: Articles that provide expert opinions, test drives, or detailed analyses of vehicles.
- Variant Launch: Introduction of a new version, trim, or edition of an existing vehicle model.

Examples:
Headline: "Honda City Sport Edition Launched at Rs. 14.89 Lakh"
Output: ["Price Change", "Variant Launch"]

Headline: "Mahindra Scorpio N to Get Panoramic Sunroof, Level 2 ADAS"
Output: ["Facelift", "Upcoming"]

Headline: "Maruti Suzuki Celebrates 2 Million Sales Milestone"
Output: ["Milestone"]

Headline: "Spyshots Reveal Next-Gen Hyundai Creta"
Output: ["Spyshots", "Upcoming"]

Headline: "Tata Motors Partners with Uber for EV Fleet Expansion"
Output: ["Partnership", "Expansion"]

Headline: "{headline}"
Output:
"""
LABELS = [
    'Corporate', 'Expansion', 'Partnership', 'Upcoming', 'New Launch',
    'Price Change', 'Event', 'Milestone', 'Facelift', 'Bookings',
    'Spyshots', 'Review', 'Variant Launch'
]


models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"]
current_model_index = 0

def get_gemini_model(model_name="gemini-2.5-flash"):
    genai.configure(api_key="AIzaSyB0cgFmqtX5oIywCWaVEPovUekVfDd_SVM")
    return genai.GenerativeModel(model_name)

def is_quota_limit_error(error_message):
    """Check if error is related to quota limits"""
    quota_keywords = [
        "quota", "limit", "exceeded", "rate limit", 
        "too many requests", "resource exhausted",
        "429", "quota exceeded", "rate_limit_exceeded"
    ]
    error_str = str(error_message).lower()
    return any(keyword in error_str for keyword in quota_keywords)

def classify_headline_gemini_with_quota_handling(headline, models, current_model_index, retries=3, delay=2):
    """
    Classify headline with automatic model switching on quota limit errors
    """
    models_tried = 0
    max_models_to_try = len(models)
    
    while models_tried < max_models_to_try:
        model_name = models[current_model_index]
        model = get_gemini_model(model_name)
        
        print(f"🤖 Using model: {model_name}")
        
        prompt = PROMPT_TEMPLATE.format(labels=", ".join(LABELS), headline=headline)
        
        for attempt in range(retries):
            try:
                response = model.generate_content(prompt)
                text = response.text.strip()
                # Extract JSON array from response
                start = text.find('[')
                end = text.find(']', start)
                if start != -1 and end != -1:
                    tags = json.loads(text[start:end+1])
                    tags = [tag for tag in tags if tag in LABELS]
                    print(f"✅ Successfully classified with {model_name}")
                    return tags, current_model_index
                else:
                    raise ValueError("No valid JSON array in response")
                    
            except Exception as e:
                error_message = str(e)
                print(f"❌ Attempt {attempt + 1} failed with {model_name}: {error_message}")
                
                # Check if it's a quota limit error
                if is_quota_limit_error(error_message):
                    print(f"🚫 Quota limit reached for {model_name}, switching to next model...")
                    break  # Switch to next model immediately
                
                # For other errors, retry with same model
                if attempt == retries - 1:
                    print(f"⚠️ Max retries reached for {model_name} (non-quota error)")
                    break  # Move to next model after max retries
                
                time.sleep(delay)
        
        # Switch to next model
        current_model_index = (current_model_index + 1) % len(models)
        models_tried += 1
        
        if models_tried < max_models_to_try:
            print(f"🔄 Switching to next model: {models[current_model_index]}")
            time.sleep(1)  # Brief pause before trying next model
    
    # If all models failed
    print("❌ All models failed for headline: " + headline[:50] + "...")
    return {"error": "All models failed"}, current_model_index
def setup_logging():
    logger = logging.getLogger(__name__)

    logger.handlers.clear()

    logging.getLogger().handlers.clear()

    logger.setLevel(logging.ERROR)

    logger.propagate = False

    # Create file handler
    file_handler = logging.FileHandler('compscrapers.log', mode='w')
    file_handler.setLevel(logging.ERROR)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
    )
    file_handler.setFormatter(formatter)

    if not logger.handlers:
        logger.addHandler(file_handler)

    return logger

# Initialize logger ONLY ONCE
if 'logger' not in globals():
    logger = setup_logging()
    logger.error("=== Logging system initialized ===")


# Initialize logger
logger = setup_logging()

# Test the logger immediately
logger.error("=== Logging system initialized ===")


# ==================== UTILITY FUNCTIONS ====================
def parse_custom_date(date_str, formats=['%B %d %Y']):
    """
    Try parsing date_str with a list of formats. Returns date as 'YYYY-MM-DD' or None.
    """
    try:
        from datetime import datetime
        # Remove the city part if present (e.g., 'Pune, ')
        if ',' in date_str:
            date_str = date_str.split(',', 1)[1].strip()
        for fmt in formats:
            try:
                date_obj = datetime.strptime(date_str, fmt)
                return date_obj.strftime('%Y-%m-%d')
            except ValueError:
                continue
        logger.error(f"Could not parse date: {date_str}")
        return None
    except Exception as e:
        logger.error(f"Error in parse_custom_date: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None

def convert_weekday_date(date_string):
    """Convert 'Thursday May 15,2025' format to yyyy-mm-dd"""
    try:
        # Parse the full string including weekday
        parsed_date = datetime.strptime(date_string, "%A %B %d,%Y")
        # Format to yyyy-mm-dd (weekday is automatically ignored)
        return parsed_date.strftime("%Y-%m-%d")
    except Exception as e:
        logger.error(f"Error in convert_weekday_date: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return date_string

def extract_date(image_url):
    try:
        match = re.search(r'(\d{2})/(\d{2})/(\d{4})/', image_url)
        if match:
            day, month, year = match.groups()
            date_str = f"{day}/{month}/{year}"
            date_obj = datetime.strptime(date_str, "%d/%m/%Y")
            return date_obj.strftime("%Y-%m-%d")
        return "No date"
    except Exception as e:
        logger.error(f"Error in extract_date: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return "No date"
# Competitors Scraper

def scrape_91wheels(max_articles=25):
    print("Scraping 91wheels...")
    articles = []
    try:
        BASE_URL = "https://www.91wheels.com"
        sections = ["/news/car-news", "/news/two-wheelers", "/news/ev"]

        for section in sections:
            if len(articles) >= max_articles:
                break
            try:
                url = BASE_URL + section
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")

                for item in soup.select("li.pt-4"):
                    try:
                        title_elem = item.find("a", class_="text-black") or item.find("a")
                        if not title_elem:
                            continue

                        title = title_elem.get("title", "").strip() or title_elem.text.strip()
                        link = BASE_URL + title_elem["href"] if title_elem["href"].startswith("/") else title_elem["href"]

                        date_elem = item.find("div", class_="text-xs text-gray-500")
                        date = date_elem.text.replace("Publish date :", "").strip() if date_elem else "No date"
                        date = datetime.strptime(date, "%d %B %Y").strftime('%Y-%m-%d')


                        article = {
                            'title': title,
                            'link': link,
                            'date': date,
                            'CompanyName': '91wheels'
                        }
                        articles.append(article)

                    except Exception as e:
                        logger.error(f"Error processing 91wheels item: {str(e)}")
                        logger.error(f"Traceback: {traceback.format_exc()}")
                        continue

            except Exception as e:
                logger.error(f"Error scraping 91wheels section {section}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue

    except Exception as e:
        logger.error(f"Error in scrape_91wheels: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return articles[:max_articles]

def scrape_bikedekho(max_articles=20):
    print("Scraping BikeDekho...")
    articles = []
    try:
        scraper = cloudscraper.create_scraper()
        response = scraper.get("https://www.bikedekho.com/news", timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        for card in soup.find_all('div', class_='card card_news shadowWPadding'):
            try:
                title_tag = card.find('h2')
                link_tag = title_tag.find('a') if title_tag else None
                if not title_tag or not link_tag:
                    continue

                title = title_tag.text.strip()
                link = f"https://www.bikedekho.com{link_tag['href']}"

                date_elem = card.find('div', class_='dotlist')
                date = date_elem.find('span').text.strip() if date_elem and date_elem.find('span') else "No date"
                date = datetime.strptime(date, "%b %d, %Y").strftime('%Y-%m-%d')

                articles.append({
                    'title': title,
                    'link': link,
                    'date': date,
                    'CompanyName': 'BikeDekho'
                })

            except Exception as e:
                logger.error(f"Error processing BikeDekho item: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue

    except Exception as e:
        logger.error(f"Error in scrape_bikedekho: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return articles[:max_articles]

def scrape_bikewale(max_articles=20):
    print("Scraping BikeWale...")
    articles = []
    try:
        response = requests.get("https://www.bikewale.com/news/",
                              headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        for li in soup.find_all('li', class_='o-b7'):
            try:
                title_tag = li.find('a', title=True)
                if not title_tag:
                    continue

                title = title_tag['title']
                link = f"https://www.bikewale.com{title_tag['href']}"

                date_elem = li.find('div', class_='o-jD o-fd')
                date = date_elem.find('p').text.strip() if date_elem and date_elem.find('p') else "No date"
                # Convert date to a yyyy-mm-dd format from relative time
                #9 days ago, 2 weeks ago, 1 month ago, etc.
                if 'minute' in date or 'hour' in date:
                            date = datetime.now().strftime('%Y-%m-%d')
                else:
                            # For days, weeks, months, we can use a simple heuristic
                            num, unit = date.split()[:2]
                            num = int(num)
                            if 'day' in unit:
                                date = (datetime.now() - timedelta(days=num)).strftime('%Y-%m-%d')
                            elif 'week' in unit:
                                date = (datetime.now() - timedelta(weeks=num)).strftime('%Y-%m-%d')
                            elif 'month' in unit:
                                date = (datetime.now() - timedelta(days=num*30)).strftime('%Y-%m-%d')


                articles.append({
                    'title': title,
                    'link': link,
                    'date': date,
                    'CompanyName': 'BikeWale'
                })

            except Exception as e:
                logger.error(f"Error processing BikeWale item: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue

    except Exception as e:
        logger.error(f"Error in scrape_bikewale: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        pass

    return articles[:max_articles]

def scrape_cardekho(max_articles=20):
    print("Scraping CarDekho...")
    articles = []
    try:
        url = "https://www.cardekho.com/india-car-news.htm"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        for card in soup.find_all('div', class_='card card_news shadowWPadding'):
            try:
                title_tag = card.find('a', title=True, href=True)
                if not title_tag:
                    continue

                title = title_tag['title'].strip()
                link = title_tag['href']
                if link.startswith('/'):
                    link = f"https://www.cardekho.com{link}"

                # Try to find date
                date = "No date"
                date_tag = card.find('span', class_='date')
                if date_tag:
                    date = date_tag.text.strip()
                else:
                    date_match = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}', card.get_text())
                    if date_match:
                        date = date_match.group(0)
                date = datetime.strptime(date, "%b %d, %Y").strftime('%Y-%m-%d')

                articles.append({
                    'title': title,
                    'link': link,
                    'date': date,
                    'CompanyName': 'CarDekho'
                })

            except Exception as e:
                logger.error(f"Error processing CarDekho item: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue

    except Exception as e:
        logger.error(f"Error in scrape_cardekho: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return articles[:max_articles]

def scrape_cars24(max_articles=20):
    print("Scraping Cars24...")
    articles = []
    try:
        url = "https://www.cars24.com/news/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        for a in soup.find_all('a', class_='relative', href=True, id=True):
            try:
                link = a['href']
                if link.startswith('/'):
                    link = f"https://www.cars24.com{link}"
                elif not link.startswith('http'):
                    link = f"https://www.cars24.com/{link.lstrip('/')}"

                if "/news/" not in link and "/auto/" not in link:
                    continue

                # Get title
                title = None
                if a.has_attr('aria-label'):
                    title = a['aria-label'].strip()
                elif a.has_attr('title'):
                    title = a['title'].strip()

                if not title:
                    h_tag = a.find(['h2', 'h3'])
                    if h_tag:
                        title = h_tag.get_text(strip=True)

                if not title:
                    title = a.get_text(strip=True)

                if not title or len(title) < 20:
                    continue

                # Get date
                date = "No date"
                for tag in a.find_all(['span', 'div']):
                    text = tag.get_text(strip=True)
                    match = re.match(r'^\d{2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$', text)
                    if match:
                        date = text
                        break

                articles.append({
                    'title': title,
                    'link': link,
                    'date': date,
                    'CompanyName': 'Cars24'
                })

            except Exception as e:
                logger.error(f"Error processing Cars24 item: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue

    except Exception as e:
        logger.error(f"Error in scrape_cars24: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return articles[:max_articles]

def scrape_carwale(max_articles=18):
    print("Scraping CarWale...")
    articles = []
    try:
        response = requests.get("https://www.carwale.com/news/",
                              headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        for li in soup.find_all('li', class_='o-b7'):
            try:
                a_tag = li.find('a', title=True, href=True)
                if not a_tag:
                    continue

                title = a_tag['title'].strip()
                link = f"https://www.carwale.com{a_tag['href']}"

                # Find date
                date = "No date"
                for div in li.find_all('div'):
                    text = div.get_text(strip=True)
                    match = re.search(r'(\d+\s+(minute|hour|day|week|month)s?\s+ago)', text)
                    if match:
                        date = match.group(1)
                        # Convert relative date to absolute date
                        if 'minute' in date or 'hour' in date:
                            date = datetime.now().strftime('%Y-%m-%d')
                        else:
                            # For days, weeks, months, we can use a simple heuristic
                            num, unit = date.split()[:2]
                            num = int(num)
                            if 'day' in unit:
                                date = (datetime.now() - timedelta(days=num)).strftime('%Y-%m-%d')
                            elif 'week' in unit:
                                date = (datetime.now() - timedelta(weeks=num)).strftime('%Y-%m-%d')
                            elif 'month' in unit:
                                date = (datetime.now() - timedelta(days=num*30)).strftime('%Y-%m-%d')
                        break

                articles.append({
                    'title': title,
                    'link': link,
                    'date': date,
                    'CompanyName': 'CarWale'
                })

            except Exception as e:
                logger.error(f"Error processing CarWale item: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue

    except Exception as e:
        logger.error(f"Error in scrape_carwale: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        pass

    return articles[:max_articles]


def create_bajaj_url(title):
    try:
        slug = re.sub(r'[^\w\s-]', '', title.lower()).strip()
        slug = re.sub(r'[-\s]+', '-', slug)
        return f"https://www.bajajauto.com/corporate/media-centre/press-releases/{slug}"
    except Exception as e:
        logger.error(f"Error in create_bajaj_url: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return "https://www.bajajauto.com/corporate/media-centre/press-releases/"

# ==================== EMAIL CONFIGURATION ====================
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587
SENDER_EMAIL = 'prabhav.varshney@collegedunia.com'
SENDER_PASSWORD = 'qzmi vwgq fdxu eznk'
RECIPIENT_EMAIL = 'vikhyaat.sharma@collegedunia.com'

# Google Sheets configuration
GOOGLE_SHEETS_ID = '13jBI2EurYBiR_QwupG9YLhvV0oRy3d-MuBjobK4HVE0'
SERVICE_ACCOUNT_FILE = 'credentials.json'
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'

]
def setup_google_sheets():
    """Initialize Google Sheets connection"""
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(GOOGLE_SHEETS_ID)
        worksheet = sheet.sheet1

        # Create headers if the sheet is empty
        existing_values = worksheet.get_all_values()
        if not existing_values or len(existing_values) == 0:
            headers = ['Source', 'Title', 'Date', 'URL', 'Added_At']
            worksheet.append_row(headers)
            print("✅ Google Sheets headers created")
        elif len(existing_values) == 1 and not any(existing_values[0]):
            headers = ['Source', 'Title', 'Date', 'URL', 'Added_At']
            worksheet.update('A1:E1', [headers])
            print("✅ Google Sheets headers updated")

        return worksheet
    except Exception as e:
        print(f"❌ Google Sheets setup error: {e}")
        return None

def add_to_google_sheets(worksheet, articles_list):
    """Add new articles to Google Sheets"""
    if not worksheet or not articles_list:
        return

    try:
        # Prepare data for Google Sheets (convert to list of lists)
        rows_to_add = []
        for article in articles_list:
            title = article['title']
            source = article['CompanyName']
            date = article['date']
            url = article['link']
            added_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            row = [source, title, date, url, added_at]
            rows_to_add.append(row)

        # Append all rows at once for better performance
        if rows_to_add:
            worksheet.append_rows(rows_to_add)
            print(f"✅ Added {len(rows_to_add)} articles to Google Sheets")

    except Exception as e:
        print(f"❌ Error adding to Google Sheets: {e}")

def send_notification_email(new_articles_list):
    """Send email notification with new articles in tabular format"""
    try:
        if not new_articles_list:
            return

        msg = MIMEMultipart('alternative')
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECIPIENT_EMAIL
        msg['Subject'] = f"🚨 {len(new_articles_list)} New Automotive News Alert(s)!"


        html_body = f"""
        <html>
        <head>
            <style>
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 20px 0;
                    font-family: Arial, sans-serif;
                }}
                th, td {{
                    border: 1px solid #ddd;
                    padding: 8px;
                    text-align: left;
                    vertical-align: top;
                }}
                th {{
                    background-color: #f2f2f2;
                    font-weight: bold;
                }}
                tr:nth-child(even) {{
                    background-color: #f9f9f9;
                }}
                .source {{
                    font-weight: bold;
                    color: #2c5aa0;
                    white-space: nowrap;
                }}
                .title {{
                    color: #333;
                    font-weight: 500;
                    max-width: 300px;
                    word-wrap: break-word;
                }}
                .date {{
                    color: #666;
                    font-size: 0.9em;
                    white-space: nowrap;
                }}
                .url {{
                    max-width: 150px;
                    word-wrap: break-word;
                }}
            </style>
        </head>
        <body>
            <h2>🚗 New Automotive News Articles Found!</h2>
            <p>Found <strong>{len(new_articles_list)}</strong> new articles from automotive websites:</p>
            <p>📊 <strong>Also added to Google Sheets:</strong> <a href="https://docs.google.com/spreadsheets/d/{GOOGLE_SHEETS_ID}" target="_blank">View Spreadsheet</a></p>

            <table>
                <thead>
                    <tr>
                        <th>Source</th>
                        <th>Title</th>
                        <th>Date</th>
                        <th>URL</th>
                    </tr>
                </thead>
                <tbody>
        """

        for article in new_articles_list:
            title = article['title']
            source = article['CompanyName']
            date = article['date']
            url = article['link']
            display_title = title[:100] + "..." if len(title) > 100 else title


            html_body += f"""
                    <tr>
                        <td class="source">{source}</td>
                        <td class="title">{display_title}</td>
                        <td class="date">{date}</td>
                        <td class="url"><a href="{url}" target="_blank">Read Article</a></td>
                    </tr>
            """

        html_body += """
                </tbody>
            </table>

            <p><em>This is an automated notification from your Automotive News Scraper.</em></p>
            <p><small>Data has been automatically added to both database and Google Sheets.</small></p>
            <p><small>Scraped on: {}</small></p>
        </body>
        </html>
        """.format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        msg.attach(MIMEText(html_body, 'html'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        to_list = [RECIPIENT_EMAIL]
        server.sendmail(SENDER_EMAIL, to_list , msg.as_string())
        server.quit()
        print(f"✅ Notification email sent successfully! ({len(new_articles_list)} new articles)")
    except Exception as e:
        logger.error(f"Error in send_notification_email: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

#===============SENDING LOGGER DATA TO EMAIL====================================

def send_session_log_email():
    """Send only current session's log entries"""
    try:
        log_file_path = 'compscrapers.log'

        if not os.path.exists(log_file_path):
            print("No log file found")
            return

        # Read entire log file
        with open(log_file_path, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()

        # Find the last session start marker
        session_start_index = -1
        session_marker = "=== Logging system initialized ==="

        # Find the LAST occurrence of session start (current session)
        for i in range(len(all_lines) - 1, -1, -1):
            if session_marker in all_lines[i]:
                session_start_index = i
                break

        if session_start_index == -1:
            print("No session start marker found")
            return

        # Get only current session logs
        current_session_logs = all_lines[session_start_index:]
        session_content = ''.join(current_session_logs)

        if len(current_session_logs) <= 1:  # Only the initialization line
            print("No errors in current session")
            return

        # Filter out initialization lines for cleaner email
        error_lines = [line for line in current_session_logs
                      if "=== Logging system initialized ===" not in line and line.strip()]

        if not error_lines:
            print("No actual errors in current session")
            return

        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECIPIENT_EMAIL
        msg['Subject'] = f"🔧 Current Session Errors - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        # Extract session timestamp from first line
        session_time = "Unknown"
        if current_session_logs:
            try:
                session_time = current_session_logs[0].split(' - ')[0]
            except:
                pass

        error_content = ''.join(error_lines)

        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                .log-content {{
                    background-color: #f5f5f5;
                    padding: 15px;
                    border-radius: 5px;
                    font-family: monospace;
                    font-size: 12px;
                    white-space: pre-wrap;
                    max-height: 500px;
                    overflow-y: auto;
                }}
                .session-info {{ background-color: #e3f2fd; padding: 10px; border-radius: 5px; margin: 10px 0; }}
                .error-count {{ color: #d32f2f; font-weight: bold; }}
            </style>
        </head>
        <body>
            <h2>🔧 Current Scraping Session Error Report</h2>

            <div class="session-info">
                <strong>Session Started:</strong> {session_time}<br>
                <strong>Total Errors in This Session:</strong> <span class="error-count">{len(error_lines)}</span>
            </div>

            <div class="log-content">
{error_content}
            </div>

            <p><strong>Report Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p><em>This email contains errors from the current scraping session only.</em></p>
        </body>
        </html>
        """

        msg.attach(MIMEText(html_body, 'html'))

        # Send email
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, ['vikhyaat.sharma@collegedunia.com'], msg.as_string())
        server.quit()

        print(f"✅ Current session error log sent successfully! ({len(error_lines)} errors)")

    except Exception as e:
        print(f"❌ Failed to send session log email: {str(e)}")






# ==================== SCRAPER FUNCTIONS ====================

def scrape_ather_energy():
    print("Scraping Ather Energy...")
    articles = []
    try:
        page = requests.get("https://press.atherenergy.com", timeout=30)
        page.raise_for_status()
        soup = BeautifulSoup(page.content, 'html.parser')

        for item in soup.find_all('div', class_='et_pb_code_inner')[:5]:
            try:
                pr_box = item.find('div', class_='pr-box')
                if not pr_box:
                    continue

                title_elem = pr_box.find('h4')
                date_elem = pr_box.find('span')

                if not title_elem or not date_elem:
                    continue

                title = title_elem.text.strip()
                date = date_elem.text.strip()

                #convert date to 'YYYY-MM-DD' format
                for fmt in ['%dth %B, %Y', '%dnd %B, %Y','%drd %B, %Y', '%dst %B, %Y', '%drd %B %Y']:
                    try:
                        date_obj = datetime.strptime(date, fmt)
                        date = date_obj.strftime('%Y-%m-%d')
                        break
                    except ValueError:
                        continue

                link_elem = item.find('a', class_='download_link')
                link = 'https://press.atherenergy.com'+link_elem['href'] if link_elem else "https://press.atherenergy.com"

                article = {
                    'title': title,
                    'link': link,
                    'date': date,
                    'CompanyName': 'Ather'
                }
                articles.append(article)

            except Exception as e:
                logger.error(f"Error processing Ather Energy item: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue

    except Exception as e:
        logger.error(f"Error in scrape_ather_energy: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return articles

def get_bmw_articles(date_formats=['%a %b %d %H:%M:%S CEST %Y', '%a %b %d %H:%M:%S CET %Y']):

    print('Scraping BMW')
    url = 'https://www.press.bmwgroup.com/india'
    articles = []
    try:
        page = requests.get(url, timeout=10)
        page.raise_for_status()

        soup = BeautifulSoup(page.text, 'html.parser')
        article_tags = soup.select('article.newsfeed')
        for each in article_tags[:5]:
            try:
                title = each.select_one('h3').get_text(strip=True)
                date_str = each.select_one('span.date').get_text(strip=True)
                date = parse_custom_date(date_str, date_formats)
                link = 'https://www.press.bmwgroup.com' + each.select_one('a')['href']
                if date is not None:
                    article = {
                        'title': title,
                        'link': link,
                        'date': date,
                        'CompanyName': 'BMW'
                    }
                    articles.append(article)
            except Exception as e:
                logger.error(f"Error extracting BMW article data: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_bmw_articles: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return articles

def get_isuzu_articles():

    articles = []
    url = 'https://www.isuzu.in/newsroom.html'
    try:
        scraper = cloudscraper.create_scraper()
        page = scraper.get(url, timeout=15)
        page.raise_for_status()

        soup = BeautifulSoup(page.text, 'html.parser')
        news_items = soup.select('div.blognews-box')
        for each in news_items:
            try:
                title = each.select_one('h4').get_text(strip=True)
                img_tag = each.select_one('img')
                img_src = img_tag['src'] if img_tag and 'src' in img_tag.attrs else (img_tag['data-src'] if img_tag and 'data-src' in img_tag.attrs else '')
                # Search for date using regex patterns
                patterns = [
                    r'\b\d{2}-[a-z]{3}-\d{4}\b',  # e.g., 12-Jun-2024
                    r'\b\d{2}\.\d{2}\.\d{4}\b',    # e.g., 12.06.2024
                    r'\b\d{1}\.\d{2}\.\d{4}\b'     # e.g., 1.06.2024
                ]
                date_str = None
                date = None
                for pattern in patterns:
                    match = re.search(pattern, img_src)
                    if match:
                        date_str = match.group(0)
                        date = parse_custom_date(date_str, ['%d-%b-%Y', '%d.%m.%Y', '%d.%m.%y'])
                        break
                link_tag = each.select_one('a')
                link = 'https://www.isuzu.in/' + link_tag['href'] if link_tag and link_tag.has_attr('href') else None
                if date is not None and link:
                    article = {
                        'title': title,
                        'link': link,
                        'date': date,
                        'CompanyName': 'Isuzu'
                    }
                    articles.append(article)
            except Exception as e:
                logger.error(f"Error extracting Isuzu article data: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_isuzu_articles: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return articles

def get_jeep_articles():

    import html
    import json

    url = 'https://www.jeep-india.com/press-release.html'
    articles = []
    try:
        page = requests.get(url, timeout=10)
        page.raise_for_status()

        soup = BeautifulSoup(page.text, 'html.parser')
        div = soup.find('div', {'data-component': 'News'})
        if not div or 'data-props' not in div.attrs:
            print("Could not find news data on the page.")
            return articles
        data_props = div['data-props']
        decoded_props = html.unescape(data_props)
        props_dict = json.loads(decoded_props)
        news_items = props_dict['newsData']['filterableList']['newsitems']['newsContent']
        for each in news_items:
            try:
                title = each['bannerDetails']['title']['value']
                date = parse_custom_date(each['bannerDetails']['preTitle']['value'], ['%d %B %Y'])
                link = 'https://www.jeep-india.com' + each['bannerDetails']['buttons'][0]['href']
                if date is not None:
                    article = {
                        'title': title,
                        'link': link,
                        'date': date,
                        'CompanyName': 'Jeep'
                    }
                    articles.append(article)
            except Exception as e:
                logger.error(f"Error extracting Jeep article data: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_jeep_articles: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return articles

def get_hero_articles():

    try:
        page = requests.get('https://www.heromotocorp.com/content/hero-aem-website/in/en-in/company/newsroom/press-release-news-and-media/jcr:content/root/container/container/company_banner.companyarticlesearch.json?searchRootPath=/content/dam/hero-aem-website/in/en-in/company-section/press-releases&results&resultsPerPage=10', timeout=10)
        page.raise_for_status()
        data_text = page.text
        data = json.loads(data_text)
        articles = []
        for each in data[:-1]:
            try:
                # Skip if it's not an article dict (e.g., {'moreMatchesExist': 'true'})
                if 'tileTitle' not in each or 'articleDate' not in each or 'pdfPath' not in each:
                    continue
                title = each['tileTitle']
                date_str = each['articleDate']
                try:
                    date = datetime.strptime(date_str, "%d %b, %Y").strftime('%Y-%m-%d')
                except Exception as e:
                    logger.error(f"Error parsing Hero date '{date_str}' for article '{title}': {str(e)}")
                    continue
                link = 'https://www.heromotocorp.com' + each['pdfPath']
                article = {
                    'title': title,
                    'link': link,
                    'date': date,
                    'CompanyName': 'Hero MotoCorp'
                }
                articles.append(article)
            except Exception as e:
                logger.error(f"Error extracting Hero article data: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
        return articles
    except Exception as e:
        logger.error(f"Error in get_hero_articles: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return []

def scrape_mg_motor():
    print("Scraping MG Motor...")
    articles = []
    try:
        response = requests.get('https://www.mgmotor.co.in/content/mgmotor/in/en/media-center/downloads.document.json', timeout=10)
        response.raise_for_status()

        for item in response.json()[:5]:
            try:
                date = item['members'][0]['dateText']
                title = item['title']
                link = item['members'][0]['mediaOriginalUrl']
                date = datetime.strptime(date, '%d %b %y').strftime('%Y-%m-%d')
                articles.append({
                    'title': title,
                    'link': link,
                    'date': date,
                    'CompanyName': 'MG Motor'
                })
            except (KeyError, IndexError, ValueError) as e:
                logger.error(f"Error processing MG Motor item: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in scrape_mg_motor: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return articles

def get_tvs_articles():

    url = 'https://www.tvsmotor.com/media/press-release'
    articles = []
    try:
        page = requests.get(url, timeout=10)
        page.raise_for_status()

        soup = BeautifulSoup(page.text, 'html.parser')
        article_tags = soup.select('div.col-xs-7')
        for each in article_tags[:10]:
            try:
                title_tag = each.select_one('a')
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                link = 'https://www.tvsmotor.com' + title_tag['href']
                date_tag = each.select_one('p')
                if not date_tag or '|' not in date_tag.get_text():
                    continue
                date_str = date_tag.get_text(strip=True).split('|')[1]
                date = datetime.strptime(date_str.strip(), '%d %b %Y').strftime('%Y-%m-%d')
                articles.append({
                    'title': title,
                    'link': link,
                    'date': date,
                    'CompanyName': 'TVS Motor'
                })
            except Exception as e:
                logger.error(f"Error extracting TVS article data: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_tvs_articles: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def scrape_bajaj_auto():
    print("Scraping Bajaj Auto...")
    articles = []
    try:
        response = requests.get('https://www.bajajauto.com/corporate/media-centre',
                              headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        for article in soup.find_all('li', class_='list-group-item')[:5]:
            try:
                ps = article.find_all('p')
                if len(ps) >= 2:
                    date = ps[1].text.strip()
                    date = datetime.strptime(date, "%b %d '%y").strftime('%Y-%m-%d')
                    title = ps[0].text.strip()
                    link = create_bajaj_url(title)
                    articles.append({
                        'title': title,
                        'link': link,
                        'date': date,
                        'CompanyName': 'Bajaj Auto'
                    })
            except Exception as e:
                logger.error(f"Error processing Bajaj Auto item: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in scrape_bajaj_auto: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def get_simple_energy_articles():

    url = 'https://api-prod.simpleenergy.in/graphql'
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9,hi;q=0.8",
        "content-type": "application/json",
        "priority": "u=1, i",
        "sec-ch-ua": "\"Chromium\";v=\"130\", \"Google Chrome\";v=\"130\", \"Not?A_Brand\";v=\"99\"",
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": "\"Android\"",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "x-api-key": "da2-3bba5n44cbhjlpb25k4ilokkuy"
    }
    data = "{\"variables\":{},\"query\":\"{\\n  getMedia(page: 1, limit: 100) {\\n    media {\\n      title\\n      article_link\\n      author\\n      source\\n      date\\n      img_path\\n      __typename\\n    }\\n    __typename\\n  }\\n}\"}"

    try:
        response = requests.post(url, data=data, headers=headers, timeout=10)
        response.raise_for_status()
        articles_json = response.json()
        articles = articles_json['data']['getMedia']['media']

        result = []
        for each in articles:
            try:
                title = each['title']
                date_str = each['date']
                date = datetime.strptime(date_str, '%m-%d-%Y').strftime('%Y-%m-%d')
                link = each['article_link']
                result.append({
                    'title': title,
                    'link': link,
                    'date': date,
                    'CompanyName': 'Simple Energy'
                })
            except Exception as e:
                logger.error(f"Error processing Simple Energy article: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
        return result
    except Exception as e:
        logger.error(f"Error in get_simple_energy_articles: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return []

def get_revolt_articles():

    url = 'https://www.revoltmotors.com/static/js/main.fb392a66.chunk.js'
    pattern = r'const\s+s\s*=\s*(\[.*?\])\s*;'
    articles = []

    try:
        page = requests.get(url, timeout=10)
        page.raise_for_status()
        js_response = page.text

        match = re.search(pattern, js_response, re.DOTALL)
        extracted_array_str = match.group(1) if match else None
        if not extracted_array_str:
            print("Could not extract articles array from JS.")
            return articles

        cleaned_str = re.sub(r',\s*\.\.\.', '', extracted_array_str)
        cleaned_str = re.sub(r'(\{|,)(\s*)([a-zA-Z0-9_]+)(\s*):', r'\1\2"\3" :', cleaned_str)

        data = json.loads(cleaned_str)
        for item in data:
            try:
                title = item['title']
                date = item['date']
                date = datetime.strptime(date, '%B %d, %Y').strftime('%Y-%m-%d')
                link = 'https://www.revoltmotors.com' + item['post_link']
                articles.append({
                    'title': title,
                    'link': link,
                    'date': date,
                    'CompanyName': 'Revolt Motors'
                })
            except Exception as e:
                logger.error(f"Error processing Revolt Motors item: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_revolt_articles: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return articles

def scrape_lexus():
    print("Scraping Lexus...")
    articles = []
    try:
        url='https://www.lexusindia.co.in/discover-lexus/news-and-events/'
        page=requests.get(url, timeout=10)
        page.raise_for_status()
        soup= BeautifulSoup(page.text, features="html.parser")
        b=soup.find_all('div', class_='news_latest_cont')

        for i in range(min(5, len(b))):
            try:
                link=b[i].a['href']
                title=b[i].find('strong').text
                date=b[i].find('small').text
                date = datetime.strptime(date, '%d %b %Y').strftime('%Y-%m-%d')
                articles.append({
                    'title': title,
                    'link': link,
                    'date': date,
                    'CompanyName': 'Lexus'
                })
            except Exception as e:
                logger.error(f"Error processing Lexus item {i}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in scrape_lexus: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return articles

def scrape_audi():
    print("Scraping Audi...")
    articles = []
    try:
        url = 'https://myaudi.in/en/news/20/press-releases'
        page = requests.get(url, timeout=10)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, features="html.parser")

        cards = soup.find_all('div', {'data-href': True})

        for i, card in enumerate(cards[:5]):
            try:
                article_url = urljoin(url, card['data-href'])

                title_elem = card.find('h2', class_='card-title name')
                title = title_elem.text.strip() if title_elem else "No title"

                date_elem = card.find('p', class_='card-text small text-muted')
                if not date_elem:
                    date_elem = card.find('p')
                date = date_elem.text.strip() if date_elem else "No date"
                date = datetime.strptime(date, '%d %b %Y').strftime('%Y-%m-%d')

                articles.append({
                    'title': title,
                    'link': article_url,
                    'date':date,
                    'CompanyName': 'Audi'
                })
            except Exception as e:
                logger.error(f"Error processing Audi card {i}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in scrape_audi: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def scrape_landrover():
    print("Scraping Land Rover...")
    articles = []
    try:
        url = 'https://www.landrover.in/explore-land-rover/news/index.html'
        page = requests.get(url, timeout=10)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, features="html.parser")
        b = soup.find_all('div', class_='textContainerTop')

        for i in range(min(5, len(b))):
            try:
                title = b[i].find('h2').text.strip()

                date = b[i].select_one('p.date')
                date = date.text.replace('Posted:', '').strip()
                date = datetime.strptime(date, '%d-%m-%Y').strftime('%Y-%m-%d')

                link = None
                parent = b[i].find_parent()
                if parent:
                    a_tag = parent.find('a', href=True)
                    if a_tag:
                        link = urljoin(url, a_tag['href'])

                if link:
                    articles.append({
                        'title': title,
                        'link': link,
                        'date': date,
                        'CompanyName': 'Land Rover'
                    })
            except Exception as e:
                logger.error(f"Error processing Land Rover item {i}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in scrape_landrover: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def scrape_kawasaki():
    print("Scraping Kawasaki...")
    articles = []
    try:
        url='https://www.kawasaki-india.com/en/news.html'
        page=requests.get(url, timeout=10)
        page.raise_for_status()
        soup= BeautifulSoup(page.text, features="html.parser")
        b=soup.find_all('div', class_='motorcycles__item')

        for i in range(min(5, len(b))):
            try:
                z=b[i].a['href']
                d='https://www.kawasaki-india.com/'+str(z)
                page2=requests.get(d, timeout=10)
                page2.raise_for_status()
                soup2= BeautifulSoup(page2.text, features="html.parser")
                e=soup2.find('div', class_='text')
                f=soup2.find('h1', class_='title__text title__text--h2')
                page_title=f.text.strip()
                page_date=e.text.strip()
                date=page_date[:len(page_date)-5]
                for fmt in ['%drd\u00a0%B %Y', '%dth %B %Y']:
                    try:
                        date_obj = datetime.strptime(date, fmt)
                        date = date_obj.strftime('%Y-%m-%d')
                        break
                    except ValueError:
                        continue
                articles.append({
                    'title': page_title,
                    'link': d,
                    'date': date,
                    'CompanyName': 'Kawasaki'
                })
            except Exception as e:
                logger.error(f"Error processing Kawasaki item {i}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in scrape_kawasaki: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def scrape_volkswagen():
    print("Scraping Volkswagen...")
    articles = []
    try:
        url='https://www.volkswagen.co.in/en/discover-volkswagen/news/news-and-updates.html'
        page=requests.get(url, timeout=10)
        page.raise_for_status()
        soup= BeautifulSoup(page.text, features="html.parser")
        b=soup.find_all('div', class_='TrackedSecondLevelTeaserElement__StyledTeaserLinkWrapper-sc-9d9b83a2-0 ziuCF')

        for i in range(min(5, len(b))):
            try:
                d=b[i].a['href']
                d='https://www.volkswagen.co.in/'+str(d)
                page2=requests.get(d, timeout=10)
                page2.raise_for_status()
                soup2= BeautifulSoup(page2.text, features="html.parser")
                e=soup2.find('span', class_='sc-dhKdcB qhBSY').text
                f=soup2.find('p').text
                for fmt in ['%B %d, %Y', '%B %d %Y']:
                    try:
                        date_obj = datetime.strptime(f, fmt)
                        date = date_obj.strftime('%Y-%m-%d')
                        break
                    except ValueError:
                        continue
                articles.append({
                    'title': e,
                    'link': d,
                    'date': date,
                    'CompanyName': 'Volkswagen'
                })
            except Exception as e:
                logger.error(f"Error processing Volkswagen item {i}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in scrape_volkswagen: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return articles

def scrape_skoda():
    print("Scraping Skoda...")
    articles = []
    try:
        url='https://www.skoda-auto.co.in/news?_kind=modulevm&_mid=ImporterV2AllNewsModule-61e7e00c&ImporterV2AllNewsModule-61e7e00c%5BitemCount%5D=8'
        page=requests.get(url, timeout=10)
        page.raise_for_status()
        info=page.json()
        c=info['ModuleViewModel']['news']

        for i in c:
            try:
                title=i["title"]
                date=i["newsDate"]
                date=date[:10]
                page_link=i["link"]["url"]

                articles.append({
                    'title': title,
                    'link': page_link,
                    'date': date,
                    'CompanyName': 'Skoda'
                })
            except Exception as e:
                logger.error(f"Error processing Skoda item: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in scrape_skoda: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def scrape_porsche():
    print("Scraping Porsche...")
    articles = []
    try:
        url='https://newsroom.porsche.com/en.html'
        page=requests.get(url, timeout=10)
        page.raise_for_status()
        soup= BeautifulSoup(page.text, features="html.parser")
        b=soup.find_all('div', class_='teaser-body')

        for i in range(min(5, len(b))):
            try:
                d=b[i].a['href']
                d='https://newsroom.porsche.com'+d
                page2=requests.get(d, timeout=10)
                page2.raise_for_status()
                soup2= BeautifulSoup(page2.text, features="html.parser")
                e=soup2.find('h1').text
                f=soup2.find('time').text
                f = datetime.strptime(f, '%d/%m/%Y').strftime('%Y-%m-%d')
                articles.append({
                    'title': e,
                    'link': d,
                    'date': f,
                    'CompanyName': 'Porsche'
                })
            except Exception as e:
                logger.error(f"Error processing Porsche item {i}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in scrape_porsche: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def scrape_toyota():
    print("Scraping toyota ...")
    append_list=[]
    try:
        url='https://www.toyotabharat.com/xml/news_list_2025.xml'
        page=requests.get(url, timeout=10)
        page.raise_for_status()
        soup= BeautifulSoup(page.content, 'xml')
        page_title=soup.find_all('title')[:5]
        date=soup.find_all('date')[:5]
        page_link=soup.find_all('url')[:5]
        for i in range(5):
            try:
                e=page_title[i].text
                finaldate=date[i].text
                finaldate=datetime.strptime(finaldate, '%B %d %Y').strftime('%Y-%m-%d')
                finallink='https://www.toyotabharat.com'+page_link[i].text
                article={
                    'title': e,
                    'link': finallink,
                    'date': finaldate,
                    'CompanyName': 'Toyota'
                }
                append_list.append(article)
            except Exception as e:
                logger.error(f"Error processing Toyota item {i}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in scrape_toyota: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return append_list

def scrape_citroen():
    print("Scraping Citroen...")
    append_list=[]
    try:
        url='https://ds-prod.citroen.in/api/press-kit?_format=json'
        page=requests.get(url, timeout=10)
        page.raise_for_status()
        info=page.json()
        c=info["content"]
        for i in range(len(c)):
            try:
                title=c[i]["field_formatted_title"]
                link=c[i]["field_unique_url"]
                link='https://www.citroen.in/press-release/detail/'+link
                pgdate=c[i]["field_realease_date"]
                finaldate=convert_weekday_date(pgdate)
                article={
                    'title': title,
                    'link': link,
                    'date': finaldate,
                    'CompanyName': 'Citroen'
                }
                append_list.append(article)
            except Exception as e:
                logger.error(f"Error processing Citroen item {i}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in scrape_citroen: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return append_list

def scrape_renault():
    print("Scraping renault...")
    append_list=[]
    try:
        url='https://media.renaultgroup.com/?lang=eng'
        page=requests.get(url, timeout=10)
        page.raise_for_status()
        soup=BeautifulSoup(page.text, features="html.parser")
        a=soup.find_all("h2")
        b=soup.find_all("div", class_="date")
        c=soup.find_all("a", class_="post-overlink")
        for i in range(3):
            try:
                page_title=a[i].text.strip()
                page_date=b[i].text
                page_link=c[i]['href']
                parsed_date = datetime.strptime(page_date, "%d/%m/%Y")
                finaldate=parsed_date.strftime("%Y-%m-%d")
                article={
                    'title': page_title,
                    'link': page_link,
                    'date': finaldate,
                    'CompanyName': 'Renault'
                }
                append_list.append(article)
            except Exception as e:
                logger.error(f"Error processing Renault item {i}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in scrape_renault: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return append_list

# ==================== TATA, MARUTI, MAHINDRA SCRAPERS ====================
tata_motors_url = 'https://www.tatamotors.com/newsroom/press-releases/'
maruti_url = 'https://www.marutisuzuki.com/corporate/media/press-releases?csrsltid=AfmBOooZ3EyPtbeQwtoIpcBV3F_6QfVaEAiZy6F7fOFRd47ml4A2AFj5'
mahindra_url = 'https://www.mahindra.com/news-room/press-release'

SCRAPER_CONFIG = {
    "tatamotors.com": {
        "base_url": tata_motors_url,
        "article_selector": "div.row.mediaBox",
        "title_selector": "h4.title",
        "url_selector": "a[href]",
        "date_selector": "p.date",
        "url_attr": "href",
        'CompanyName' : 'Tata Motors',
    },
    "marutisuzuki.com": {
        "base_url": maruti_url,
        "article_selector": "li.list-group-item",
        "title_selector": "a.articletitle",
        "url_selector": "a.articletitle",
        "date_selector_month": "div.cl-block-month",
        "date_selector_day": "div.cl-block-date",
        "url_attr": "href",
        'year_selector' : 'div.year span',
        'CompanyName' : 'Maruti Suzuki',
    },
    "mahindra.com": {
        "base_url": mahindra_url,
        "article_selector": "div.grid-box",
        "title_selector": "div.desc h2",
        "url_selector": "a",
        "date_selector": "div.date time",
        "url_attr": "href",
        'CompanyName' : 'Mahindra',
    }
}

def scrape_articles(domain, config):
    articles = []
    try:
        res = requests.get(config["base_url"], timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        year = None
        if 'year_selector' in config:
            year_tag = soup.select_one(config['year_selector'])
            if year_tag:
                year = year_tag.get_text(strip=True)

        for item in soup.select(config["article_selector"])[:5]:
            try:
                title_tag = item.select_one(config["title_selector"])
                url_tag = item.select_one(config["url_selector"])

                if not title_tag or not url_tag:
                    continue

                # Case 1: single date tag (Tata Motors style)
                if "date_selector" in config:
                    date_tag = item.select_one(config["date_selector"])
                    if not date_tag:
                        continue
                    raw_date = date_tag.get_text(strip=True)
                    date_match = re.search(r"\b\d{1,2} \w+ \d{4}\b", raw_date)
                    if date_match:
                        date = date_match.group(0)
                    else:
                        date = raw_date
                # Case 2: month + day + (optional) external year (Maruti Suzuki style)
                elif "date_selector_month" in config and "date_selector_day" in config:
                    month_tag = item.select_one(config["date_selector_month"])
                    day_tag = item.select_one(config["date_selector_day"])
                    if not (month_tag and day_tag):
                        continue
                    date = f"{month_tag.get_text(strip=True)} {day_tag.get_text(strip=True)}"
                    if year:
                        date += f", {year}"
                else:
                    continue

                for fmt in ['%B %d, %Y', '%b %d, %Y','%d %B %Y']:
                    try:
                        date_obj = datetime.strptime(date, fmt)
                        date = date_obj.strftime('%Y-%m-%d')
                        break
                    except ValueError:
                        continue

                title = title_tag.get_text(strip=True)
                url = urljoin(config["base_url"], url_tag[config["url_attr"]])
                company_name = config['CompanyName']

                articles.append({
                    "title": title,
                    "link": url,
                    "date": date,
                    'CompanyName': company_name
                })
            except Exception as e:
                logger.error(f"Error processing article for {domain}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in scrape_articles for {domain}: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def get_tata_maruti_mahindra_news():
    print("Scraping Tata Motors, Maruti Suzuki, and Mahindra news...")
    all_articles = []
    try:
        for site, config in SCRAPER_CONFIG.items():
            site_url = config['base_url']
            articles = scrape_articles(site_url, config)
            all_articles.extend(articles)
    except Exception as e:
        logger.error(f"Error in get_tata_maruti_mahindra_news: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return all_articles

def get_hyu_news():
    print("Fetching Hyundai news...")
    articles = []
    try:
        data = {
            'loc':'IN',
            'lang':'en',
            'newsType':'L',
            'lan':'en'
        }
        hyu_page = requests.post('https://www.hyundai.com/wsvc/in/spa/common/news/newsList.html', data=data, timeout=10)
        hyu_page.raise_for_status()

        for article in hyu_page.json()[:5]:
            try:
                title = article.get("title")
                link = 'https://www.hyundai.com/in/en/hyundai-story/media-center/india-news'
                date = article.get("reg_date")
                articles.append({
                    "title": title,
                    "link": link,
                    "date": date,
                    'CompanyName': 'Hyundai'
                })
            except Exception as e:
                logger.error(f"Error processing Hyundai article: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_hyu_news: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def get_kia_news():
    print("Fetching Kia news...")
    articles = []
    try:
        data = {
            "pageNo": 1,
            "pageSize": 50,
        }
        res = requests.post('https://www.kia.com/api/kia2_in/news.getNewsList.do', data=data, timeout=10)
        res.raise_for_status()

        kia_list = res.json().get('data', {}).get('newsList', [])
        for each in kia_list[:5]:
            try:
                title = each.get('title')
                date = each.get('metaKeywords')
                for fmt in ['%B %d, %Y']:
                    try:
                        date_obj = datetime.strptime(date, fmt)
                        date = date_obj.strftime('%Y-%m-%d')
                        break
                    except ValueError:
                        continue
                url = 'https://www.kia.com/in/discover-kia/news/news-pr/detail.html?id=' + str(each.get('id'))
                articles.append({
                    "title": title,
                    "link": url,
                    "date": date,
                    'CompanyName': 'kia'
                })
            except Exception as e:
                logger.error(f"Error processing Kia article: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_kia_news: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def get_byd_news():
    print("Fetching BYD news...")
    articles = []
    try:
        byd_page = requests.get('https://api.bydautoindia.com/news', timeout=10)
        byd_page.raise_for_status()

        for each in byd_page.json()['data'][:5]:
            try:
                title = each['title']
                date= each['news_date']
                for fmt in ['%d-%m-%Y', '%Y-%m-%d']:
                    try:
                        date_obj = datetime.strptime(date, fmt)
                        date = date_obj.strftime('%Y-%m-%d')
                        break
                    except ValueError:
                        continue
                url = 'https://www.bydautoindia.com/news/' + each['sku']
                articles.append({
                    "title": title,
                    "link": url,
                    "date": date,
                    'CompanyName': 'byd'
                })
            except Exception as e:
                logger.error(f"Error processing BYD article: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_byd_news: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def get_vin_news():
    print("Fetching VinFast news...")
    articles = []
    try:
        scraper = cloudscraper.create_scraper()
        vin_page = scraper.get('https://vinfastauto.in', timeout=10)

        vin_soup = BeautifulSoup(vin_page.text, 'html.parser')
        story_items = vin_soup.select('div.story-item')

        for item in story_items[:5]:
            try:
                title = item.select_one('div.title').get_text(strip=True)
                url = 'https://vinfastauto.in' + item.select_one('a')['href']
                date = item.select_one('div.date').get_text(strip=True)
                date = datetime.strptime(date, '%m.%d.%Y').strftime('%Y-%m-%d')
                articles.append({
                    "title": title,
                    "link": url,
                    "date": date,
                    'CompanyName': 'vin'
                })
            except Exception as e:
                logger.error(f"Error processing VinFast article: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_vin_news: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def get_mi_news():
    print("Fetching Xiaomi news...")
    articles = []
    try:
        mi_page = requests.get('https://go.buy.mi.com/global/page/discovery?from=mobile&page_num=1&show_type=newsroom', timeout=10)
        mi_page.raise_for_status()
        page_data_mi = mi_page.json()['data']['page_data']

        for each in page_data_mi[:5]:
            try:
                article = each['assembly_info'][0]
                title = article['title']
                image_url = article['image_url']
                date = extract_date(image_url)
                url = article['go_to_url']
                articles.append({
                    "title": title,
                    "link": url,
                    "date": date,
                    'CompanyName': 'mi'
                })
            except Exception as e:
                logger.error(f"Error processing Xiaomi article: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_mi_news: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def get_force_news():
    print("Fetching Force Motors news...")
    articles = []
    try:
        force_page = requests.get('https://www.forcemotors.com/media-events/', timeout=10)
        force_page.raise_for_status()

        force_soup = BeautifulSoup(force_page.text, 'html.parser')

        article_divs = force_soup.select('div.press-release-card')

        for each in article_divs[:5]:
            try:
                title = each.select_one('span.news-room-title').get_text(strip=True)
                url = 'https://forcemotors.com' + each.select_one('a')['href']
                date_str = each.select_one('div.news-room-date').get_text(strip=True)
                for fmt in ['%B %d, %Y']:
                    try:
                        date = datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
                        break
                    except ValueError:
                        continue

                articles.append({
                    "title": title,
                    "link": url,
                    "date": date,
                    'CompanyName': 'force'
                })
            except Exception as e:
                logger.error(f"Error parsing Force Motors article: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                #print(f"Error parsing Force Motors article: {str(e)}")
                continue
    except Exception as e:
        logger.error(f"Error in get_force_news: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        #print(f"Error in get_force_news: {str(e)}")
    return articles
def get_yamaha_news():
    print("Fetching Yamaha news...")
    y_news = []
    try:
        y_page = requests.get('https://www.yamaha-motor-india.com/news-latest.html', timeout=10)
        y_page.raise_for_status()
        y_soup = BeautifulSoup(y_page.content, 'html.parser')

        articles_block = y_soup.select_one('div.year-content')
        if not articles_block:
            return y_news

        year = articles_block.get('id')[-4:]
        articles = articles_block.select('div.Persistence')

        for each in articles:
            try:
                month = each.select_one('h4').get_text(strip=True)
                day = each.select_one('h6').get_text(strip=True)
                title = each.select_one('h5').get_text(strip=True)
                url = urljoin('https://www.yamaha-motor-india.com', each.select_one('a')['href'])
                date = f"{month} {day} {year}"
                date = datetime.strptime(date, '%b %d %Y').strftime('%Y-%m-%d')
                y_news.append({
                    "title": title,
                    "link": url,
                    "date": date,
                    'CompanyName': 'yamaha'
                })
            except Exception as e:
                logger.error(f"Error processing Yamaha article: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_yamaha_news: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return y_news

def get_suzuki_news():
    print("Fetching Suzuki Motorcycle news...")
    news = []
    try:
        scraper = cloudscraper.create_scraper()
        suz_page = scraper.get('https://www.suzukimotorcycle.co.in/media', timeout=10)
        soup = BeautifulSoup(suz_page.text, 'html.parser')
        articles = soup.select('div.accordion-title')

        for each in articles[:10]:
            try:
                title = each.select_one('div.col-md-11').get_text(strip=True)
                url = 'https://www.suzukimotorcycle.co.in/media#' + title.lower().replace(' ', '-').replace('?', '').replace('!', '').replace(',', '').replace("'","")
                date = each.select_one('div.dateDM').get_text(strip=True)
                year = each.select_one('div.dateY').get_text(strip=True)
                date = f"{date} {year}"
                date = datetime.strptime(date, '%d-%b %Y').strftime('%Y-%m-%d')
                news.append({
                    "title": title,
                    "link": url,
                    "date": date,
                    'CompanyName': 'suzuki'
                })
            except Exception as e:
                logger.error(f"Error processing Suzuki article: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_suzuki_news: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return news

def get_ktm_news():
    print("Fetching KTM news...")
    news = []
    try:
        ktm_page = requests.get('https://www.ktm.com/en-in/news/_jcr_content/root/responsivegrid_1_col/newslist.news-query.json', timeout=10)
        ktm_page.raise_for_status()
        articles = ktm_page.json()['newsTeaserItems']

        for each in articles:
            try:
                title = each['title']
                url = each['url']
                date = each['releaseDate']
                date = datetime.strptime(date, '%d-%b-%Y').strftime('%Y-%m-%d')
                news.append({
                    "title": title,
                    "link": url,
                    "date": date,
                    'CompanyName': 'ktm'
                })
            except Exception as e:
                logger.error(f"Error processing KTM article: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_ktm_news: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return news

def get_bounce_news():
    print("Fetching Bounce news...")
    articles = []
    try:
        data = {"operationName":"Query","variables":{"pagination":{"pageSize":12,"page":1}},"query":"query Query($pagination: PaginationArg, $filters: SocialMediaFiltersInput) {\n  socialMedias(pagination: $pagination, filters: $filters, sort: \"createdAt:DESC\") {\n    data {\n      id\n      attributes {\n        description\n        image {\n          data {\n            id\n            attributes {\n              url\n              __typename\n            }\n            __typename\n          }\n          __typename\n        }\n        slug\n        name\n        createdAt\n        updatedAt\n        publishedAt\n        __typename\n      }\n      __typename\n    }\n    meta {\n      pagination {\n        total\n        __typename\n      }\n      __typename\n    }\n    __typename\n  }\n}"}
        bounce_page = requests.post('https://strapi.bounce.bike/graphql', json=data, timeout=10)
        bounce_page.raise_for_status()
        data = bounce_page.json()['data']['socialMedias']['data']

        for each in data:
            try:
                title = each['attributes']['name']
                date = each['attributes']['createdAt'][:10]
                url = 'https://bounceinfinity.com/socialmedia/' + each['attributes']['slug']
                articles.append({
                    "title": title,
                    "link": url,
                    "date": date,
                    'CompanyName': 'bounce'
                })
            except Exception as e:
                logger.error(f"Error processing Bounce article: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue
    except Exception as e:
        logger.error(f"Error in get_bounce_news: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    return articles

def get_all_news():
    """
    Fetch news from all sources and return a combined list of articles.
    """
    all_news = []
    try:
        all_news.extend(scrape_ather_energy())
        all_news.extend(get_bmw_articles())
        all_news.extend(get_isuzu_articles())
        all_news.extend(get_jeep_articles())
        all_news.extend(get_tvs_articles())
        all_news.extend(get_hero_articles())
        all_news.extend(scrape_bajaj_auto())
        all_news.extend(get_simple_energy_articles())
        all_news.extend(get_revolt_articles())
        all_news.extend(scrape_lexus())
        all_news.extend(scrape_audi())
        all_news.extend(scrape_landrover())
        all_news.extend(scrape_kawasaki())
        all_news.extend(scrape_volkswagen())
        all_news.extend(scrape_skoda())
        all_news.extend(scrape_porsche())
        all_news.extend(scrape_toyota())
        all_news.extend(scrape_citroen())
        all_news.extend(scrape_renault())
        all_news.extend(get_tata_maruti_mahindra_news())
        all_news.extend(get_hyu_news())
        all_news.extend(get_kia_news())
        all_news.extend(get_byd_news())
        all_news.extend(get_vin_news())
        all_news.extend(get_mi_news())
        all_news.extend(get_force_news())
        all_news.extend(get_yamaha_news())
        all_news.extend(get_suzuki_news())
        all_news.extend(get_ktm_news())
        all_news.extend(get_bounce_news())
        all_news.extend(scrape_mg_motor())
        all_news.extend(scrape_91wheels())
        # all_news.extend(scrape_carwale())
        all_news.extend(scrape_cardekho())
        #all_news.extend(scrape_bikewale())
        #all_news.extend(scrape_cars24()) 
        all_news.extend(scrape_bikedekho())

    except Exception as e:
        logger.error(f"Error in get_all_news: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return all_news

# def is_duplicate(cursor, title, date):
#     """Check if article already exists in database"""
#     try:
#         cursor.execute("SELECT 1 FROM final WHERE title = ? AND date = ?", (title, date))
#         return cursor.fetchone() is not None
#     except Exception as e:
#         logger.error(f"Error in is_duplicate: {str(e)}")
#         logger.error(f"Traceback: {traceback.format_exc()}")
#         return False

def setup_database():
    try:
        import mysql.connector
        conn = mysql.connector.connect(
            host="207.244.254.107",
            user="cto",
            password="v7#9mYvaJVSuWKZ",
            database="news_notifications"
        )
        cursor = conn.cursor()

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS final (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        title VARCHAR(500),
        link VARCHAR(1024),
        date DATE,
        CompanyName VARCHAR(255),
        UNIQUE(title(100), link(200), date, CompanyName(100))
        );''')
        conn.commit()
        conn.close()
        print("✅ Database setup completed")
    except Exception as e:
        logger.error(f"Error in setup_database: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

def check_new_news_send_mail(all_articles):
    """Check for new articles and send email notification"""
    try:
        
        conn = mysql.connector.connect(
            host="207.244.254.107",
            user="cto",
            password="v7#9mYvaJVSuWKZ",
            database="news_notifications"
        )
        c = conn.cursor()


        new_articles = []
        print(len(all_articles), "articles found in all sources")
        #sort the articles by date
        #all_articles.sort(key=lambda x: x['date'], reverse=True)
        for article in all_articles:
            try:
                title = article['title']
                link = article['link']
                date = article['date']
                CompanyName = article['CompanyName']
                # Insert into DB
                c.execute(
                    "INSERT INTO final (title, link, date, CompanyName) VALUES (%s, %s, %s, %s)",
                    (title, link, date, CompanyName)
                )
                conn.commit()
                print(f"Inserted article: {title} on {date} from {CompanyName}")
                #append if added in db
                if c.rowcount > 0:
                    new_articles.append({
                        "title": title,
                        "link": link,
                        "date": date,
                        "CompanyName": CompanyName
                    })
            except Exception as e:
                #logger.error(f"Error processing article in check_new_news_send_mail: {str(e)}")
                #logger.error(f"Traceback: {traceback.format_exc()}")
                pass

        conn.close()


        if new_articles:
            send_notification_email(new_articles)
            print(f"✅ Found {len(new_articles)} new articles and sent notification")
            worksheet = setup_google_sheets()
            #add_to_google_sheets(worksheet, new_articles)
        else:
            print("No new articles found")

        return new_articles

    except Exception as e:
        #logger.error(f"Error in check_new_news_send_mail: {str(e)}")
        #logger.error(f"Traceback: {traceback.format_exc()}")
        print(f"Error in check_new_news_send_mail: {str(e)}")

def save_all_news_to_file(all_news):
    """Save all articles to JSON file"""
    try:
        with open('all_news.json', 'w', encoding='utf-8') as f:
            json.dump(all_news, f, indent=4, ensure_ascii=False)
        print(f"✅ Saved {len(all_news)} articles to all_news.json")
    except Exception as e:
        logger.error(f"Error in save_all_news_to_file: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

def append_to_google_sheet(articles, sheet_id, worksheet_name, credentials_file):
    """
    Append articles with tags to a Google Sheet. 
    """
    try:
        # Connect to Google Sheets
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_file, scope)
            client = gspread.authorize(creds)
            sheet = client.open_by_key(sheet_id)
            worksheet = sheet.worksheet(worksheet_name)
        except Exception as e:
            logger.error(f"Error connecting to Google Sheets: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return

        # Prepare header if not present
        headers = ["CompanyName", "title", "date", "url", "tags"]
        try:
            if worksheet.row_count < 1 or worksheet.row_values(1) != headers:
                worksheet.insert_row(headers, 1)
        except Exception as e:
            logger.error(f"Error preparing headers in Google Sheet: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")

        # Prepare rows
        rows = []
        for article in articles:
            try:
                row = [
                    article.get("CompanyName", ""),
                    article.get("title", ""),
                    article.get("date", ""),
                    article.get("link", ""),
                    ", ".join(article.get("tags", []))
                ]
                rows.append(row)
            except Exception as e:
                logger.error(f"Error preparing row for article: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                continue

        # Append rows to the worksheet
        try:
            if rows:
                worksheet.append_rows(rows, value_input_option='USER_ENTERED')
        except Exception as e:
            logger.error(f"Error appending rows to Google Sheet: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")

    except Exception as e:
        logger.error(f"Error in append_to_google_sheet: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return




# ==================== MAIN EXECUTION ====================
if __name__ == "__main__":
    try:
        logger.info("=== Starting Automotive News Scraper ===")

        # Setup database
        setup_database()

        # Get all news
        print(" Starting news collection from all sources...")
        all_news = get_all_news()
        print(f" Total articles collected: {len(all_news)}")

        # Save to file
        save_all_news_to_file(all_news)



        # Check for new articles and send notifications
        print("🔍 Checking for new articles...")
        new_articles = check_new_news_send_mail(all_news)
        print(f" Found {len(new_articles)} new articles")
        # Process articles with quota-aware model switching
        tags_list = []
        failed_articles = []

        for i, article in enumerate(new_articles):
            print(f"\n📰 Processing article {i+1}/{len(new_articles)}: {article['title'][:50]}...")
            
            tags, current_model_index = classify_headline_gemini_with_quota_handling(
                article['title'], 
                models, 
                current_model_index
            )
            tags = []
            
            tags_list.append(tags)
            article['tags'] = tags
            
            if isinstance(article['tags'], dict) and 'error' in article['tags']:
                logger.error(f"Error classifying article {article['title']}: {article['tags']['error']}")
                article['tags'] = []
                failed_articles.append(article['title'])
                continue
            
            print(f"🏷️ Tags assigned: {tags}")
            
            # Add small delay between requests to avoid hitting rate limits
            time.sleep(0.5)

        print(f"\n📊 Processing Summary:")
        print(f"Total articles processed: {len(new_articles)}")
        successful_tags = sum(1 for article in new_articles if isinstance(article['tags'], list) and len(article['tags']) > 0)
        print(f"Successfully tagged: {successful_tags}")
        print(f"Failed to tag: {len(failed_articles)}")

        if failed_articles:
            print(f"Failed articles: {failed_articles}")


        print(f"📝 Classified {len(new_articles)} new articles with tags")
        append_to_google_sheet(new_articles, '13jBI2EurYBiR_QwupG9YLhvV0oRy3d-MuBjobK4HVE0', 'Sheet4', 'credentials.json')
        print("✅ Appended new articles to Google Sheet")


        conn = mysql.connector.connect(
            host="207.244.254.107",
            user="cto",
            password="v7#9mYvaJVSuWKZ",
            database="news_notifications"
        )
        cursor = conn.cursor()
        cursor.execute('''DROP TABLE IF EXISTS newarticles''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS newarticles (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        title VARCHAR(500),
        link VARCHAR(1024),
        date DATE,
        CompanyName VARCHAR(255),
        tags VARCHAR(255),
        UNIQUE(title(100), link(200), date, CompanyName(100))
        );''')
        for article in new_articles:
            try:
                title = article['title']
                link = article['link']
                date = article['date']
                CompanyName = article['CompanyName']
                tags = ', '.join(article.get('tags', []))

                # Insert into DB
                cursor.execute(
                    "INSERT INTO newarticles (title, link, date, CompanyName, tags) VALUES (%s, %s, %s, %s, %s)",
                    (title, link, date, CompanyName, tags)
                )
                # Insert tags as JSON in final table
                try:
                    tags_json = json.dumps({"tags": article.get('tags', [])}, ensure_ascii=False)
                    cursor.execute(
                        "UPDATE final SET tags = %s WHERE title = %s AND link = %s AND date = %s AND CompanyName = %s",
                        (tags_json, title, link, date, CompanyName)
                    )
                except Exception as e:
                    logger.error(f"Error updating tags in final table: {str(e)}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    continue

            except Exception as e:
                #logger.error(f"Error processing article in check_new_news_send_mail: {str(e)}")
                #logger.error(f"Traceback: {traceback.format_exc()}")
                continue

    

        


        conn.commit()
        conn.close()


        send_session_log_email()

        print("✅ Scraping completed successfully!")

    except Exception as e:
        logger.error(f"Fatal error in main execution: {str(e)}")
        logger.error(f"Main traceback: {traceback.format_exc()}")
        print(f"❌ Fatal error occurred. Check log for details.")

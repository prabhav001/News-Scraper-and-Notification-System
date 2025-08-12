import sqlite3
import pandas as pd
from newspaper import Article
import google.generativeai as genai
import time
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from datetime import datetime, timedelta

# Configure Gemini API
genai.configure(api_key="AIzaSyCsO2XQm1GAa9P312yDhhnX2KaM80JsGQw")

def get_article(url):
    try:
        article = Article(url)
        article.download()
        article.parse()
        return {
            'title': article.title,
            'text': article.text,
            'authors': article.authors,
            'publish_date': article.publish_date
        }
    except Exception as e:
        print("Error fetching article from " + url + ": " + str(e))
        return None

def generate_automotive_article(title: str, content: str = None) -> str:
    """Generate new content using Gemini API"""
    system_instruction = (
        "You are an expert automotive journalist. "
        "Given a news title, or a news title with its full article content, generate a professional, fact-based news article for the automotive industry. "
        "If content is provided, use it as the primary source and rewrite it in your own words, improving clarity and journalistic quality. "
        "Structure the article with an introduction, technical details, market analysis, and a conclusion. "
        "Include simulated quotes from manufacturers or industry experts, and provide technical specifications if relevant. "
        "Keep the article around 50-100 words and write in 3 paragraphs. "
        "Make sure the text is human-like, not just a list of facts."
    )
    try:
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=system_instruction
        )
        if content:
            prompt = (
                "NEWS TITLE: " + title + "\n" +
                "ARTICLE CONTENT: " + content + "\n" +
                "Rewrite and summarize this article as a professional automotive journalist."
            )
        else:
            prompt = "NEWS TITLE: " + title + "\nWrite a professional news article based on this title."
        
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.7,
                max_output_tokens=2048
            )
        )
        return response.text
    except Exception as e:
        return "Error generating content: " + str(e)

def setup_google_sheets(sheet_id, credentials_file):
    """Setup Google Sheets connection"""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_file, scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(sheet_id)
        
        try:
            worksheet = sheet.worksheet("Sheet6")
            print("Successfully connected to Sheet6 by name")
        except:
            try:
                worksheet = sheet.get_worksheet(3)
                print("Successfully connected to Sheet4 by index 3")
            except:
                try:
                    worksheet = sheet.get_worksheet(1)
                    print("Connected to worksheet at index 1")
                except:
                    worksheet = sheet.get_worksheet(0)
                    print("Using first available worksheet")
        
        return worksheet
    except Exception as e:
        print("Error setting up Google Sheets: " + str(e))
        return None

def add_columns_to_sheet(worksheet):
    """Add content and recommendations columns to Google Sheet if they don't exist"""
    try:

        headers = worksheet.row_values(1)
        print("Current headers: " + str(headers))
        

        if 'content' not in headers:
            col_index = len(headers) + 1
            worksheet.update_cell(1, col_index, 'content')
            print("Added 'content' column to Google Sheet")
        

        headers = worksheet.row_values(1)
        if 'recommendations' not in headers:
            col_index = len(headers) + 1
            worksheet.update_cell(1, col_index, 'recommendations')
            print("Added 'recommendations' column to Google Sheet")
            
    except Exception as e:
        print("Error adding columns: " + str(e))

def filter_recent_news(df, max_age_days=365, date_col='date'):
    """Filter news articles by date"""
    today = datetime.today()
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    return df[df[date_col] >= today - timedelta(days=max_age_days)].reset_index(drop=True)

def recommend_similar_news_from_dict(
    df,
    article_dict,
    top_n=3,
    max_age_days=365,
    boost_shared_tags=0.1,
    boost_shared_company=0.1,
    filter_company=False,
    diversity=True
):
    try:

        recent_df = filter_recent_news(df, max_age_days)
        recent_df['CompanyName'] = recent_df['CompanyName'].str.strip().str.lower()
        target_company = article_dict['CompanyName'].strip().lower()
        target_title = article_dict['title']
        target_tags = [tag.strip() for tag in article_dict['Tags']]
        target_content = article_dict.get('content', '')


        if filter_company:
            recent_df = recent_df[recent_df['CompanyName'] == target_company].reset_index(drop=True)


        target_title_tags = target_title + " " + target_content + " " + ' '.join(target_tags)


        recent_df['title_tags'] = (
            recent_df['title'] + ' ' +
            recent_df['content'].fillna('') + ' ' +
            recent_df['tags'].apply(lambda tags: ' '.join(tags) if isinstance(tags, list) else str(tags))
        )
        


        all_texts = [target_title_tags] + recent_df['title_tags'].tolist()
        vectorizer = TfidfVectorizer(stop_words='english')
        tfidf_matrix = vectorizer.fit_transform(all_texts)


        cosine_sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:]).flatten()



        target_tags_set = set(target_tags)
        for i, row in recent_df.iterrows():
            row_tags = row['Tags'] if isinstance(row['Tags'], list) else str(row['Tags']).split(',')
            if target_tags_set.intersection(set(row_tags)):
                cosine_sim[i] += boost_shared_tags


        for i, row in recent_df.iterrows():
            if row['CompanyName'] == target_company:
                cosine_sim[i] += boost_shared_company


        if diversity:
            seen_titles = set()
            indices_sorted = cosine_sim.argsort()[::-1]
            unique_indices = []
            for idx in indices_sorted:
                title = recent_df.iloc[idx]['title']
                if title not in seen_titles and cosine_sim[idx] > 0:
                    unique_indices.append(idx)
                    seen_titles.add(title)
                if len(unique_indices) == top_n:
                    break
        else:
            unique_indices = cosine_sim.argsort()[-top_n:][::-1]


        output_cols = ['title', 'tags', 'date', 'CompanyName', 'url']
        available_cols = [col for col in output_cols if col in recent_df.columns]
        results = recent_df.iloc[unique_indices][available_cols].to_dict(orient='records')
        return results, cosine_sim[unique_indices]
    except Exception as e:
        print("Error in recommendation function: " + str(e))
        return [], []

def fetch_articles_from_database(db_path="official\\final.db"):
    """Fetch all articles from database and generate recommendations"""
    try:

        # conn = sqlite3.connect(db_path)
        # newarticles_df = pd.read_sql_query("SELECT * FROM newarticles", conn)
        # conn.close()
        # print("Fetched " + str(len(newarticles_df)) + " articles from database")
        

        try:
            all_tags_df = pd.read_csv('official/bahut_saare_tags.csv')
            all_tags_df = all_tags_df.dropna(subset=['Tags'])
            all_tags_df['Tags'] = all_tags_df['Tags'].apply(lambda x: [tag.strip() for tag in str(x).split(',')])
            all_tags_df['CompanyName'] = all_tags_df['CompanyName'].str.strip().str.lower()
            print("Loaded " + str(int(len(all_tags_df))) + " reference articles for recommendations")
        except Exception as e:
            print("Error loading reference data: " + str(e))
            all_tags_df = pd.DataFrame()
        

        # newarticles_df['Tags'] = newarticles_df['Tags'].apply(lambda x: [tag.strip() for tag in str(x).split(',')])
        # newarticles_df['CompanyName'] = newarticles_df['CompanyName'].str.strip().str.lower()
        
        #create df for bahut_saare_tags.csv
        a=pd.read_csv('official/bahut_saare_tags.csv')
        a['Tags'] = a['Tags'].apply(lambda x: [tag.strip() for tag in str(x).split(',')])
        a['CompanyName'] = a['CompanyName'].str.strip().str.lower()

        # Generate recommendations for each article

        recommendations_list = []
        
        for idx, row in a.iterrows():
            article_dict = row.to_dict()
            article_dict = article_dict.copy()
            
            try:
                if not all_tags_df.empty:
                    recs, scores = recommend_similar_news_from_dict(
                        all_tags_df,
                        article_dict=article_dict,
                        top_n=3,
                        max_age_days=365*10,
                        boost_shared_tags=0.1,
                        boost_shared_company=0.1,
                        filter_company=False,
                        diversity=True
                    )
                    rec_titles = [r['title'] for r in recs]
                    recommendations_list.append('; '.join(rec_titles))
                else:
                    recommendations_list.append("No reference data available")
            except Exception as e:
                recommendations_list.append("Error: " + str(e))
        

        #newarticles_df['Recommendations'] = recommendations_list
        a['Recommendations'] = recommendations_list
        
        return a.to_dict('records')
        
    except Exception as e:
        print("Error fetching articles from database: " + str(e))
        return []

def update_google_sheet_with_content_and_recommendations(worksheet, article_title, generated_content, recommendations):
    """Update Google Sheet with generated content and recommendations by matching title"""
    try:

        all_data = worksheet.get_all_records()
        

        for row_index, row_data in enumerate(all_data, start=2):
            sheet_title = str(row_data.get('title', '')).strip()
            db_title = str(article_title).strip()
            

            title_match = (
                sheet_title.lower() == db_title.lower() or
                sheet_title.lower() in db_title.lower() or
                db_title.lower() in sheet_title.lower()
            )
            
            if title_match:
                headers = worksheet.row_values(1)
                

                if 'content' in headers:
                    content_col_index = headers.index('content') + 1
                    worksheet.update_cell(row_index, content_col_index, generated_content)
                
                if 'recommendations' in headers:
                    rec_col_index = headers.index('recommendations') + 1
                    worksheet.update_cell(row_index, rec_col_index, recommendations)
                
                print("Updated content and recommendations for: " + article_title[:50] + "...")
                return True
                
        print("Article title not found in Google Sheet: " + article_title[:50] + "...")
        return False
        
    except Exception as e:
        print("Error updating Google Sheet: " + str(e))
        return False

def process_articles_and_update_google_sheet_with_recommendations(
    db_path="official\\final.db", 
    sheet_id="13jBI2EurYBiR_QwupG9YLhvV0oRy3d-MuBjobK4HVE0", 
    credentials_file="official/credentials.json"
):
    """Process articles from database and update Google Sheet with content and recommendations"""
    
    worksheet = setup_google_sheets(sheet_id, credentials_file)
    if not worksheet:
        print("Failed to setup Google Sheets connection")
        return 0
    
    add_columns_to_sheet(worksheet)
    
    articles = fetch_articles_from_database(db_path)
    
    # if not articles:
    #     print("No articles found in database")
    #     return 0
    
    processed_count = 0
    successful_scrapes = 0
    successful_generations = 0
    
    
    for i, article in enumerate(articles):
        print("Processing article " + str(i+1) + "/" + str(len(articles)) + ": " + article.get('title', 'Unknown Title'))
        
        generated_content = ""
        recommendations = article.get('Recommendations', 'No recommendations available')
        
        url = article.get('link')
        if url:
            scraped_data = get_article(url)
            
            if scraped_data:
                successful_scrapes += 1
                title_for_generation = scraped_data['title'] or article.get('title', '')
                #generated_content = generate_automotive_article(title_for_generation, scraped_data['text'])
                #print("Successfully processed: " + title_for_generation)
            else:
                pass
                #title_for_generation = article.get('title', '')
                #if title_for_generation:
                    #generated_content = generate_automotive_article(title_for_generation)
                    #print("Scraping failed, generated from title only: " + title_for_generation)
                #else:
                    #generated_content = "Error: No content available for generation"
                    #print("Failed to process article: No URL or title available")
        else:
            generated_content = "Error: No URL provided"
            print("No URL found in article: " + str(article.get('id', 'Unknown ID')))
        
        if generated_content and not generated_content.startswith('Error'):
            successful_generations += 1
        
        success = update_google_sheet_with_content_and_recommendations(
            worksheet, 
            article.get('title'), 
            generated_content, 
            recommendations
        )
        if success:
            processed_count += 1
        
        time.sleep(2)
    
    print("\nProcessing Summary:")
    print("Total articles processed: " + str(processed_count))
    print("Successful content extractions: " + str(successful_scrapes))
    print("Successful content generations: " + str(successful_generations))
    
    return processed_count

if __name__ == "__main__":
    print("Starting article processing with content generation and recommendations...")
    
    DB_PATH = "official\\final.db"
    SHEET_ID = "13jBI2EurYBiR_QwupG9YLhvV0oRy3d-MuBjobK4HVE0"
    CREDENTIALS_FILE = "official/credentials.json"
    
    processed_count = process_articles_and_update_google_sheet_with_recommendations(DB_PATH, SHEET_ID, CREDENTIALS_FILE)
    
    if processed_count > 0:
        print("\nProcessing completed successfully!")
        print("Generated content and recommendations have been added to Google Sheet.")
    else:
        print("Articles were processed but titles didn't match between database and Google Sheet.")

import sys
import site
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from datetime import datetime, timedelta
import sqlite3
import smtplib
from email.mime.text import MIMEText
import os

# Spotify credentials are retrieved from environment variables
client_id = os.getenv("SPOTIFY_CLIENT_ID")
client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI")

if not client_id or not client_secret or not redirect_uri:
    print("Error: Missing Spotify API credentials.")
    sys.exit(1)

# SQLite database files
USERS_DB_FILE = "users.db"
DATABASE_FILE_TEMPLATE = "liked_songs_{}.db"

# Email credentials
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")

# Spotipy client setup
def setup_spotify():
    """Set up Spotify client with necessary permissions"""
    try:
        return spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope="user-library-read"
        ))
    except Exception as e:
        print(f"Error setting up Spotify client: {e}")
        sys.exit(1)

def setup_users_database():
    """Set up SQLite database for user management"""
    conn = sqlite3.connect(USERS_DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            spotify_user_id TEXT PRIMARY KEY,
            name TEXT,
            email TEXT,
            signup_date TEXT
        )
    """)
    conn.commit()
    return conn

def setup_user_songs_database(spotify_user_id):
    """Set up a user-specific SQLite database for liked songs"""
    db_file = DATABASE_FILE_TEMPLATE.format(spotify_user_id)
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS liked_songs (
            track_id TEXT PRIMARY KEY,
            track_name TEXT,
            artist_name TEXT,
            added_at TEXT,
            fetch_date TEXT
        )
    """)
    conn.commit()
    return conn

def send_email(to_email, subject, content):
    """Send an email to the user"""
    try:
        msg = MIMEText(content)
        msg['Subject'] = subject
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, to_email, msg.as_string())

        print(f"Email sent to {to_email}")
    except Exception as e:
        print(f"Error sending email to {to_email}: {e}")

def get_liked_songs(sp):
    """Fetch all liked songs from the user's library"""
    tracks = []
    offset = 0
    total = None
    
    try:
        while True:
            results = sp.current_user_saved_tracks(limit=50, offset=offset)
            if total is None:
                total = results['total']
                print(f"Found {total} tracks to process")
            
            if not results['items']:
                break
            
            for item in results['items']:
                track = item['track']
                tracks.append({
                    'id': track['id'],
                    'name': track['name'],
                    'artist': track['artists'][0]['name'],
                    'added_at': item['added_at']
                })
            
            offset += 50
            print(f"Progress: {min(offset, total)}/{total} tracks", end='\r')
        
        print("\nDone fetching tracks")
        return tracks
    
    except Exception as e:
        print(f"\nError fetching tracks: {e}")
        sys.exit(1)

def save_tracks_to_db(conn, tracks):
    """Save liked songs to the SQLite database"""
    cursor = conn.cursor()
    fetch_date = datetime.now().strftime("%Y-%m-%d")
    for track in tracks:
        cursor.execute("""
            INSERT OR REPLACE INTO liked_songs (track_id, track_name, artist_name, added_at, fetch_date)
            VALUES (?, ?, ?, ?, ?)
        """, (track['id'], track['name'], track['artist'], track['added_at'], fetch_date))
    conn.commit()
    print(f"Tracks saved to database on {fetch_date}")

def compare_tracks(conn, email):
    """Compare current tracks with the last fetch and notify the user of changes"""
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT fetch_date FROM liked_songs ORDER BY fetch_date DESC LIMIT 2")
    dates = [row[0] for row in cursor.fetchall()]

    if len(dates) < 2:
        print("Not enough data to compare.")
        return

    latest_date, previous_date = dates
    print(f"Comparing {latest_date} with {previous_date}")

    # Find removed tracks
    cursor.execute("""
        SELECT track_name, artist_name FROM liked_songs
        WHERE fetch_date = ? AND track_id NOT IN (
            SELECT track_id FROM liked_songs WHERE fetch_date = ?
        )
    """, (previous_date, latest_date))
    removed_tracks = cursor.fetchall()

    # Find added tracks
    cursor.execute("""
        SELECT track_name, artist_name FROM liked_songs
        WHERE fetch_date = ? AND track_id NOT IN (
            SELECT track_id FROM liked_songs WHERE fetch_date = ?
        )
    """, (latest_date, previous_date))
    added_tracks = cursor.fetchall()

    # Prepare email content
    content = f"Hi,\n\nHere are the changes to your Spotify Liked Songs:\n\n"
    if removed_tracks:
        content += "Removed Tracks:\n" + "\n".join(f"- {t[0]} by {t[1]}" for t in removed_tracks) + "\n"
    else:
        content += "No tracks have been removed.\n"

    if added_tracks:
        content += "\nAdded Tracks:\n" + "\n".join(f"+ {t[0]} by {t[1]}" for t in added_tracks) + "\n"
    else:
        content += "\nNo tracks have been added.\n"

    # Send notification
    send_email(email, "Spotify Liked Songs Updates", content)

def main():
    # Set up Spotify client and databases
    sp = setup_spotify()
    users_conn = setup_users_database()

    # Get all users
    cursor = users_conn.cursor()
    cursor.execute("SELECT spotify_user_id, email FROM users")
    users = cursor.fetchall()

    # Process each user
    for user_id, email in users:
        print(f"Processing user {user_id}...")
        user_conn = setup_user_songs_database(user_id)

        # Fetch and save current liked songs
        print("\nFetching current liked songs...")
        current_tracks = get_liked_songs(sp)
        save_tracks_to_db(user_conn, current_tracks)

        # Compare tracks and send email notifications
        compare_tracks(user_conn, email)
        user_conn.close()

    users_conn.close()

if __name__ == "__main__":
    main()

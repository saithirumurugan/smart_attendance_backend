import mysql.connector
from mysql.connector import Error

def get_db_connection():
    try:
        connection = mysql.connector.connect(
            host='localhost',
            database='smart_attendance',
            user='root',
            password='' # Set empty for local dev, or whatever the standard is
        )
        if connection.is_connected():
            return connection
    except Error as e:
        print(f"Error while connecting to MySQL: {e}")
    return None

def init_db():
    try:
        # Connect without database first to create it
        connection = mysql.connector.connect(
            host='localhost',
            user='root',
            password=''
        )
        cursor = connection.cursor()
        cursor.execute("CREATE DATABASE IF NOT EXISTS smart_attendance")
        cursor.execute("USE smart_attendance")
        
        # Create Tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id VARCHAR(50) PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                department VARCHAR(100) NOT NULL,
                face_encoding JSON NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INT AUTO_INCREMENT PRIMARY KEY,
                student_id VARCHAR(50),
                date DATE NOT NULL,
                time TIME NOT NULL,
                status VARCHAR(20) DEFAULT 'Present',
                FOREIGN KEY (student_id) REFERENCES students(id),
                UNIQUE KEY unique_attendance (student_id, date)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                setting_key VARCHAR(50) PRIMARY KEY,
                setting_value VARCHAR(255) NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS holidays (
                id INT AUTO_INCREMENT PRIMARY KEY,
                holiday_date DATE UNIQUE NOT NULL,
                description VARCHAR(255)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                action VARCHAR(100) NOT NULL,
                details TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backups (
                id INT AUTO_INCREMENT PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                filename VARCHAR(255) NOT NULL
            )
        """)
        
        # Seed default admin (password: admin123)
        # Using werkzeug hash format for simplicity
        from werkzeug.security import generate_password_hash
        default_hash = generate_password_hash('admin123')
        try:
            cursor.execute("INSERT IGNORE INTO admins (username, password_hash) VALUES (%s, %s)", ('admin', default_hash))
        except Error as e:
            pass # Ignore if exists

        # Seed default settings
        default_settings = [
            ('class_start_time', '09:00'),
            ('late_threshold', '09:15'),
            ('absent_threshold', '09:30')
        ]
        for key, val in default_settings:
            try:
                cursor.execute("INSERT IGNORE INTO system_settings (setting_key, setting_value) VALUES (%s, %s)", (key, val))
            except Error as e:
                pass

        
        connection.commit()
        print("Database initialized successfully.")
    except Error as e:
        print(f"Error initializing DB: {e}")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

if __name__ == '__main__':
    init_db()

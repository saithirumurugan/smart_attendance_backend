from flask import Flask, request, jsonify
from flask_cors import CORS
from db import get_db_connection
from face_service import get_face_encoding_from_base64, match_face
import json
import datetime

app = Flask(__name__)
CORS(app)

@app.route('/api/status', methods=['GET'])
def status():
    return jsonify({"status": "running"})

@app.route('/api/register', methods=['POST'])
def register_student():
    data = request.json
    student_id = data.get('student_id')
    name = data.get('name')
    department = data.get('department')
    face_image = data.get('face_image') # base64
    
    if not all([student_id, name, department, face_image]):
        return jsonify({"error": "All fields are required"}), 400
        
    encoding, error = get_face_encoding_from_base64(face_image)
    if error:
        return jsonify({"error": error}), 400
        
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
        
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM students WHERE id = %s", (student_id,))
        if cursor.fetchone():
            return jsonify({"error": "Student ID already exists"}), 400
            
        cursor.execute(
            "INSERT INTO students (id, name, department, face_encoding) VALUES (%s, %s, %s, %s)",
            (student_id, name, department, json.dumps(encoding))
        )
        conn.commit()
        return jsonify({"message": "Student registered successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/attendance', methods=['POST'])
def mark_attendance():
    data = request.json
    face_image = data.get('face_image') # base64
    
    if not face_image:
        return jsonify({"error": "Face image is required"}), 400
        
    encoding, error = get_face_encoding_from_base64(face_image)
    if error:
        return jsonify({"error": error}), 400
        
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
        
    try:
        # We need a dictionary cursor to access by column name, but mysql-connector-python dictionary=True works differently, so let's use standard cursor
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, face_encoding FROM students")
        students = cursor.fetchall()
        
        if not students:
            return jsonify({"error": "No registered students found"}), 404
            
        known_encodings = []
        student_records = []
        for student in students:
            enc = json.loads(student[2])
            known_encodings.append(enc)
            student_records.append(student)
            
        match_index = match_face(encoding, known_encodings)
        if match_index is None:
            return jsonify({"status": "Unknown Person", "message": "Face not recognized"}), 200
            
        matched_student = student_records[match_index]
        student_id = matched_student[0]
        name = matched_student[1]
        
        now = datetime.datetime.now()
        date_str = now.strftime('%Y-%m-%d')
        time_str = now.strftime('%H:%M:%S')
        
        # Check for duplicate
        cursor.execute("SELECT id FROM attendance WHERE student_id = %s AND date = %s", (student_id, date_str))
        if cursor.fetchone():
            return jsonify({
                "status": "Already Marked", 
                "student_id": student_id,
                "name": name,
                "message": f"Attendance already marked for {name} today"
            }), 200
            
        # Mark attendance
        cursor.execute(
            "INSERT INTO attendance (student_id, date, time, status) VALUES (%s, %s, %s, %s)",
            (student_id, date_str, time_str, 'Present')
        )
        conn.commit()
        
        return jsonify({
            "status": "Success",
            "student_id": student_id,
            "name": name,
            "time": time_str,
            "message": f"Attendance marked for {name}"
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/reports', methods=['GET'])
def get_reports():
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({"error": "Date parameter is required"}), 400
        
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
        
    try:
        cursor = conn.cursor()
        query = """
            SELECT a.student_id, s.name, s.department, a.date, a.time, a.status 
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            WHERE a.date = %s
            ORDER BY a.time DESC
        """
        cursor.execute(query, (date_str,))
        records = cursor.fetchall()
        
        results = []
        for row in records:
            results.append({
                "student_id": row[0],
                "name": row[1],
                "department": row[2],
                "date": str(row[3]),
                "time": str(row[4]),
                "status": row[5]
            })
            
        return jsonify(results), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

from flask import Flask, request, jsonify
from flask_cors import CORS
from db import get_db_connection
from face_service import get_face_encoding_from_base64, get_face_encoding_for_registration, match_face
import json
import datetime
import jwt
from functools import wraps
from werkzeug.security import check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_super_secret_key_change_in_production'
CORS(app)

# Tolerance for mock (OpenCV + LBP) mode vs real face_recognition mode.
# LBP cosine distance: 0.0 = identical person, higher = more different.
# 0.15 gives a good balance for LBP to avoid false "Unknown Person" rejections.
MOCK_TOLERANCE = 0.15   # LBP cosine distance — balanced for accuracy
REAL_TOLERANCE = 0.45   # euclidean — strict threshold for accurate recognition

try:
    import face_recognition
    TOLERANCE = REAL_TOLERANCE
except ImportError:
    TOLERANCE = MOCK_TOLERANCE


@app.route('/api/status', methods=['GET'])
def status():
    return jsonify({"status": "running"})

@app.route('/api/admin/settings', methods=['GET', 'POST'])
def manage_settings():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    try:
        cursor = conn.cursor()
        if request.method == 'GET':
            cursor.execute("SELECT setting_key, setting_value FROM system_settings")
            settings = {row[0]: row[1] for row in cursor.fetchall()}
            return jsonify(settings)
        else:
            data = request.json
            for key, val in data.items():
                cursor.execute(
                    "INSERT INTO system_settings (setting_key, setting_value) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)",
                    (key, val)
                )
            conn.commit()
            return jsonify({"message": "Settings updated successfully"})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()



@app.route('/api/register', methods=['POST'])
def register_student():
    data = request.json
    student_id = data.get('student_id', '').strip()
    name       = data.get('name', '').strip()
    department = data.get('department', '').strip()
    face_image = data.get('face_image')

    if not all([student_id, name, department, face_image]):
        return jsonify({"error": "All fields are required"}), 400
        
    enc, error = get_face_encoding_for_registration(face_image)
    if error:
        return jsonify({"error": error}), 400
        
    # Wrap in list to maintain schema compatibility with recent multi-sample changes
    encodings = [enc]

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # Check duplicate student ID
        cursor.execute("SELECT id FROM students WHERE id = %s", (student_id,))
        if cursor.fetchone():
            return jsonify({"error": "Student ID already exists"}), 400

        # Check if same face already registered using the first encoding
        cursor.execute("SELECT id, name, face_encoding FROM students")
        existing = cursor.fetchall()

        if existing:
            known_encodings = [json.loads(s[2]) for s in existing]
            match_index, dist = match_face(encodings[0], known_encodings, tolerance=TOLERANCE)
            if match_index is not None:
                matched_name = existing[match_index][1]
                return jsonify({"error": f"Face already registered for '{matched_name}'."}), 400

        cursor.execute(
            "INSERT INTO students (id, name, department, face_encoding) VALUES (%s, %s, %s, %s)",
            (student_id, name, department, json.dumps(encodings))
        )
        conn.commit()
        print(f"[Register] New student: {name} ({student_id})")
        return jsonify({"message": f"Student '{name}' registered successfully!"})

    except Exception as e:
        print(f"[Error] register: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route('/api/validate_face', methods=['POST'])
def validate_face():
    data = request.json
    face_image = data.get('face_image')

    if not face_image:
        return jsonify({"error": "No image provided"}), 400

    print("[Validate] Received face image for validation.")
    encoding, error = get_face_encoding_for_registration(face_image)

    if error:
        print(f"[Validate] Face capture failed: {error}")
        return jsonify({"error": error}), 400

    print("[Validate] Face successfully detected and valid.")
    return jsonify({"message": "Valid"}), 200


@app.route('/api/attendance', methods=['POST'])
def mark_attendance():
    data = request.json
    face_image = data.get('face_image')

    if not face_image:
        return jsonify({"error": "Face image is required"}), 400

    encoding, error = get_face_encoding_from_base64(face_image)

    if error:
        print(f"[Attendance] Error: {error}")
        if "Multiple faces" in error:
            return jsonify({
                "status": "Multiple Faces",
                "message": "Multiple faces detected. Please stand alone."
            }), 200
        if "Low lighting" in error:
            return jsonify({
                "status": "Low Light",
                "message": "Low lighting detected. Please move to a brighter area."
            }), 200
            
        # No face detected in the frame
        return jsonify({
            "status": "No Face",
            "message": "No face detected"
        }), 200

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, face_encoding FROM students")
        students = cursor.fetchall()

        if not students:
            return jsonify({
                "status": "No Students",
                "message": "No registered students in database"
            }), 200

        known_encodings  = [json.loads(s[2]) for s in students]
        student_records  = list(students)

        match_index, best_distance = match_face(encoding, known_encodings, tolerance=TOLERANCE)

        if match_index is None:
            print(f"[Attendance] Unknown person. Best dist={best_distance}")
            return jsonify({
                "status": "Unknown Person",
                "message": "Face not recognized — please register first.",
                "distance": round(best_distance, 4) if best_distance else None
            }), 200

        matched = student_records[match_index]
        student_id = matched[0]
        name       = matched[1]

        now      = datetime.datetime.now()
        date_str = now.strftime('%Y-%m-%d')
        time_str = now.strftime('%H:%M:%S')

        # Check if already marked today
        cursor.execute(
            "SELECT id FROM attendance WHERE student_id = %s AND date = %s",
            (student_id, date_str)
        )
        if cursor.fetchone():
            print(f"[Attendance] Already marked: {name}")
            return jsonify({
                "status": "Already Marked",
                "student_id": student_id,
                "name": name,
                "time": time_str,
                "message": f"Attendance already marked for {name} today"
            }), 200

        # Calculate Status based on Settings
        cursor.execute("SELECT setting_key, setting_value FROM system_settings WHERE setting_key IN ('late_threshold', 'absent_threshold')")
        settings = {row[0]: row[1] for row in cursor.fetchall()}
        
        status = 'Present'
        late_thresh = settings.get('late_threshold', '09:15')
        absent_thresh = settings.get('absent_threshold', '09:30')
        
        # Simple string comparison works for HH:MM format
        current_hm = now.strftime('%H:%M')
        if current_hm > absent_thresh:
            status = 'Absent'
        elif current_hm > late_thresh:
            status = 'Late Present'

        # Insert attendance
        cursor.execute(
            "INSERT INTO attendance (student_id, date, time, status) VALUES (%s, %s, %s, %s)",
            (student_id, date_str, time_str, status)
        )
        conn.commit()

        print(f"[Attendance] Marked PRESENT: {name} ({student_id}) at {time_str} | dist={best_distance:.4f}")

        return jsonify({
            "status": "Success",
            "attendance_status": status,
            "student_id": student_id,
            "name": name,
            "time": time_str,
            "message": f"Welcome, {name}! Attendance marked."
        }), 200

    except Exception as e:
        print(f"[Error] mark_attendance: {e}")
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
        # Get all students and left join with attendance for the specific date
        query = """
            SELECT s.id, s.name, s.department, a.date, a.time, a.status
            FROM students s
            LEFT JOIN attendance a ON s.id = a.student_id AND a.date = %s
            ORDER BY s.id
        """
        cursor.execute(query, (date_str,))
        records = cursor.fetchall()

        results = []
        for row in records:
            status = row[5]
            if not status:
                status = 'Absent' # Default to absent if no record found
                
            results.append({
                "student_id": row[0],
                "name":       row[1],
                "department": row[2],
                "date":       str(row[3]) if row[3] else date_str,
                "time":       str(row[4]) if row[4] else "-",
                "status":     status
            })

        return jsonify(results), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/admin/dashboard', methods=['GET'])
def get_dashboard_stats():
    date_str = datetime.datetime.now().strftime('%Y-%m-%d')
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM students")
        total_students = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM attendance WHERE date = %s AND status = 'Present'", (date_str,))
        present_students = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM attendance WHERE date = %s AND status IN ('Late', 'Late Present')", (date_str,))
        late_students = cursor.fetchone()[0]

        absent_students = total_students - (present_students + late_students)
        if total_students > 0:
            attendance_percentage = round(((present_students + late_students) / total_students) * 100, 2)
        else:
            attendance_percentage = 0

        return jsonify({
            "total_students": total_students,
            "present_students": present_students,
            "absent_students": absent_students,
            "late_students": late_students,
            "attendance_percentage": attendance_percentage
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/admin/holidays', methods=['GET', 'POST', 'DELETE'])
def manage_holidays():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        if request.method == 'GET':
            cursor.execute("SELECT id, holiday_date, description FROM holidays ORDER BY holiday_date")
            holidays = [{"id": row[0], "date": str(row[1]), "description": row[2]} for row in cursor.fetchall()]
            return jsonify(holidays), 200
            
        elif request.method == 'POST':
            data = request.json
            h_date = data.get('date')
            desc = data.get('description', '')
            if not h_date:
                return jsonify({"error": "Date is required"}), 400
            cursor.execute("INSERT INTO holidays (holiday_date, description) VALUES (%s, %s)", (h_date, desc))
            conn.commit()
            return jsonify({"message": "Holiday added successfully"}), 201
            
        elif request.method == 'DELETE':
            h_id = request.args.get('id')
            if not h_id:
                return jsonify({"error": "Holiday ID required"}), 400
            cursor.execute("DELETE FROM holidays WHERE id = %s", (h_id,))
            conn.commit()
            return jsonify({"message": "Holiday deleted"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()
@app.route('/api/admin/backup', methods=['POST'])
def create_backup():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        backup_data = {}
        
        # Dump Students
        cursor.execute("SELECT id, name, department, face_encoding, created_at FROM students")
        backup_data['students'] = [{
            "id": row[0], "name": row[1], "department": row[2], 
            "face_encoding": row[3], "created_at": str(row[4])
        } for row in cursor.fetchall()]

        # Dump Attendance
        cursor.execute("SELECT id, student_id, date, time, status FROM attendance")
        backup_data['attendance'] = [{
            "id": row[0], "student_id": row[1], "date": str(row[2]), 
            "time": str(row[3]), "status": row[4]
        } for row in cursor.fetchall()]

        # Dump Settings
        cursor.execute("SELECT setting_key, setting_value FROM system_settings")
        backup_data['settings'] = {row[0]: row[1] for row in cursor.fetchall()}

        # Save to file
        import os
        if not os.path.exists('backups'):
            os.makedirs('backups')
            
        filename = f"backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join('backups', filename)
        
        with open(filepath, 'w') as f:
            json.dump(backup_data, f)
            
        cursor.execute("INSERT INTO backups (filename) VALUES (%s)", (filename,))
        conn.commit()

        return jsonify({"message": "Backup created successfully", "filename": filename}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/admin/backups', methods=['GET'])
def list_backups():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, timestamp, filename FROM backups ORDER BY timestamp DESC")
        backups = [{"id": row[0], "timestamp": str(row[1]), "filename": row[2]} for row in cursor.fetchall()]
        return jsonify(backups), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

from flask import Flask, request, jsonify
from flask_cors import CORS
from db import get_db_connection
from face_service import get_face_encoding_from_base64, get_face_encoding_for_registration, match_face
import json
import datetime

app = Flask(__name__)
CORS(app)

# Tolerance for mock (OpenCV cosine) mode vs real face_recognition mode
MOCK_TOLERANCE = 0.30   # cosine distance — tuned for histogram encoding
REAL_TOLERANCE = 0.58   # euclidean — handles all expressions

try:
    import face_recognition
    TOLERANCE = REAL_TOLERANCE
except ImportError:
    TOLERANCE = MOCK_TOLERANCE


@app.route('/api/status', methods=['GET'])
def status():
    return jsonify({"status": "running"})


@app.route('/api/register', methods=['POST'])
def register_student():
    data = request.json
    student_id = data.get('student_id', '').strip()
    name       = data.get('name', '').strip()
    department = data.get('department', '').strip()
    face_image = data.get('face_image')

    if not all([student_id, name, department, face_image]):
        return jsonify({"error": "All fields are required"}), 400

    encoding, error = get_face_encoding_for_registration(face_image)
    if error:
        return jsonify({"error": f"Face capture failed: {error}"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # Check duplicate student ID
        cursor.execute("SELECT id FROM students WHERE id = %s", (student_id,))
        if cursor.fetchone():
            return jsonify({"error": "Student ID already exists"}), 400

        # Check if same face already registered
        cursor.execute("SELECT id, name, face_encoding FROM students")
        existing = cursor.fetchall()

        if existing:
            known_encodings = [json.loads(s[2]) for s in existing]
            match_index, dist = match_face(encoding, known_encodings, tolerance=TOLERANCE)
            if match_index is not None:
                matched_name = existing[match_index][1]
                return jsonify({"error": f"Face already registered for '{matched_name}'."}), 400

        cursor.execute(
            "INSERT INTO students (id, name, department, face_encoding) VALUES (%s, %s, %s, %s)",
            (student_id, name, department, json.dumps(encoding))
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


@app.route('/api/attendance', methods=['POST'])
def mark_attendance():
    data = request.json
    face_image = data.get('face_image')

    if not face_image:
        return jsonify({"error": "Face image is required"}), 400

    encoding, error = get_face_encoding_from_base64(face_image)

    if error:
        # No face detected in the frame
        print(f"[Attendance] No face: {error}")
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

        # Insert attendance
        cursor.execute(
            "INSERT INTO attendance (student_id, date, time, status) VALUES (%s, %s, %s, %s)",
            (student_id, date_str, time_str, 'Present')
        )
        conn.commit()

        print(f"[Attendance] Marked PRESENT: {name} ({student_id}) at {time_str} | dist={best_distance:.4f}")

        return jsonify({
            "status": "Success",
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
        query = """
            SELECT a.student_id, s.name, s.department, a.date, a.time, a.status
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            WHERE a.date = %s
            ORDER BY a.time DESC
        """
        cursor.execute(query, (date_str,))
        records = cursor.fetchall()

        results = [{
            "student_id": row[0],
            "name":       row[1],
            "department": row[2],
            "date":       str(row[3]),
            "time":       str(row[4]),
            "status":     row[5]
        } for row in records]

        return jsonify(results), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

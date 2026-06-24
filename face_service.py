import cv2
import numpy as np
import base64
import logging

try:
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False
    logging.warning("face_recognition module not found. Face matching will use a mock implementation.")

def get_face_encoding_from_base64(base64_image):
    """
    Decodes a base64 image (typically from frontend webcam capture)
    and returns its face encoding if a face is detected.
    """
    try:
        # Remove header if present (e.g. data:image/jpeg;base64,)
        if ',' in base64_image:
            base64_image = base64_image.split(',')[1]
            
        img_data = base64.b64decode(base64_image)
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if not FACE_RECOGNITION_AVAILABLE:
            # Mock implementation: just return a dummy 128-d vector
            # based on the average color of the image to simulate some variance
            avg_color = img.mean(axis=(0, 1))
            mock_encoding = np.full(128, avg_color.mean() / 255.0).tolist()
            return mock_encoding, None

        # Convert BGR to RGB (face_recognition uses RGB)
        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        # Detect faces
        face_locations = face_recognition.face_locations(rgb_img)
        if not face_locations:
            return None, "No face detected in the image."
            
        if len(face_locations) > 1:
            return None, "Multiple faces detected. Please ensure only one face is in the frame."
            
        # Get encodings
        face_encodings = face_recognition.face_encodings(rgb_img, face_locations)
        if face_encodings:
            return face_encodings[0].tolist(), None
            
        return None, "Could not extract face features."
    except Exception as e:
        return None, str(e)

def match_face(unknown_encoding, known_encodings, tolerance=0.6):
    """
    Compares an unknown face encoding against a list of known encodings.
    Returns the index of the matched face or None if no match found.
    """
    try:
        if not known_encodings:
            return None
            
        # known_encodings is a list of lists representing vectors
        known_encodings_np = [np.array(enc) for enc in known_encodings]
        unknown_encoding_np = np.array(unknown_encoding)
        
        if not FACE_RECOGNITION_AVAILABLE:
            # Mock implementation: calculate simple euclidean distance
            for i, known_enc in enumerate(known_encodings_np):
                dist = np.linalg.norm(known_enc - unknown_encoding_np)
                # Since our mock encoding is basic, just accept very similar vectors
                if dist < 1.0: # Arbitrary threshold for mock
                    return i
            return None

        results = face_recognition.compare_faces(known_encodings_np, unknown_encoding_np, tolerance=tolerance)
        if True in results:
            first_match_index = results.index(True)
            return first_match_index
        return None
    except Exception as e:
        print(f"Error matching face: {e}")
        return None

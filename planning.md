# Smart Attendance System using Face Recognition

## How it Works (Step by Step)

### 1. Register Student

Store student name, ID, and face photo in database.

### 2. Capture Face

Camera takes live image or video.

### 3. Face Detection

System finds the face in the image.

### 4. Face Recognition

Compares the face with stored faces.

### 5. Mark Attendance

If matched, attendance is saved automatically.

### 6. Store Record

Save date and time in database.

---

## What is Smart Attendance?

Attendance is taken by looking at your face.

No need to:

* Sign in paper
* Tell your name
* Enter roll number

The system itself checks your face and marks attendance.

---

## Before Using It

First, teacher must save student face.

That is called **Face Registration**.

---

## Uses

✅ Saves time
✅ No manual attendance
✅ Reduces fake attendance
✅ Easy tracking

---

# Technologies Used

## 1. Python

Main programming language.

### Purpose:

* Write the full project logic
* Connect camera, database, and face recognition

---

## 2. OpenCV

Used for camera and image processing.

### Purpose:

* Capture face from webcam
* Detect face in image/video

### Example:

Camera ON → OpenCV reads the face.

---

## 3. face_recognition

Used to identify whose face it is.

### Purpose:

* Compare live face with saved faces
* Match the student

### Example:

Checks: "Is this Arun’s face?"

---

## 4. MySQL

Used to store attendance data.

### Stores:

* Student name
* Date
* Time
* Attendance status

---

## 5. Google Antigravity IDE

Used to write and run code.

---

## 6. Webcam / Camera

Used to scan faces.

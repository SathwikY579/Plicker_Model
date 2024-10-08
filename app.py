import cv2
import numpy as np
from flask import Flask, render_template_string, request
from flask_socketio import SocketIO
import threading
import base64
import logging
from utils import process_frame, encode_frame
from collections import OrderedDict

app = Flask(__name__)
socketio = SocketIO(app, async_mode='threading')

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Global variables
student_count = 0
stream_active = False
detected_students = OrderedDict()


# HTML template
html_template = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live Plicker Detection</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <style>
        body { 
            font-family: Arial, sans-serif; 
            max-width: 800px; 
            margin: 0 auto; 
            padding: 20px; 
            background-color: #f0f0f0;
        }
        h1, h2 { color: #333; }
        #video-feed { 
            max-width: 100%; 
            height: auto; 
            border: 2px solid #ddd;
            border-radius: 4px;
        }
        #results, #live-results, #final-results { 
            margin-top: 20px; 
            background-color: white;
            padding: 15px;
            border-radius: 4px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        #detection-info { 
            position: absolute; 
            top: 10px; 
            right: 10px; 
            background-color: #333;
            color: white;
            padding: 10px;
            border-radius: 4px;
            max-width: 200px;
        }
        #unique-count { 
            font-size: 18px; 
            font-weight: bold; 
            margin-bottom: 5px;
        }
        #recognized-names {
            font-size: 14px;
            max-height: 100px;
            overflow-y: auto;
        }
        .control-panel { 
            margin-bottom: 20px; 
            display: flex;
            gap: 10px;
        }
        button { 
            padding: 10px 20px; 
            font-size: 16px; 
            background-color: #4CAF50;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
        }
        button:disabled {
            background-color: #ddd;
            cursor: not-allowed;
        }
        input[type="number"] {
            padding: 10px;
            font-size: 16px;
            border: 1px solid #ddd;
            border-radius: 4px;
        }
        table { 
            width: 100%; 
            border-collapse: collapse; 
            margin-top: 10px;
        }
        th, td { 
            border: 1px solid #ddd; 
            padding: 12px; 
            text-align: left; 
        }
        th { 
            background-color: #f2f2f2; 
            font-weight: bold;
        }
        tr:nth-child(even) {
            background-color: #f9f9f9;
        }
    </style>
</head>
<body>
    <h1>Live Plicker Detection</h1>
    <div class="control-panel">
        <input type="number" id="student-count" placeholder="Enter number of students">
        <button id="start-btn">Start Stream</button>
        <button id="stop-btn" disabled>Stop Stream</button>
    </div>
    <div id="detection-info">
        <div id="unique-count"></div>
        <div id="recognized-names"></div>
    </div>
    <img id="video-feed" src="" alt="Video Feed">
    <div id="live-results"></div>
    <div id="final-results"></div>

    <script>
        const socket = io();
        const videoFeed = document.getElementById('video-feed');
        const liveResults = document.getElementById('live-results');
        const finalResults = document.getElementById('final-results');
        const uniqueCount = document.getElementById('unique-count');
        const recognizedNames = document.getElementById('recognized-names');
        const startBtn = document.getElementById('start-btn');
        const stopBtn = document.getElementById('stop-btn');
        const studentCountInput = document.getElementById('student-count');

        let detectedStudents = {};

        startBtn.addEventListener('click', () => {
            const count = studentCountInput.value;
            if (count > 0) {
                socket.emit('start_stream', {count: count});
                startBtn.disabled = true;
                stopBtn.disabled = false;
                finalResults.innerHTML = '';
                detectedStudents = {};
            } else {
                alert('Please enter a valid number of students');
            }
        });

        stopBtn.addEventListener('click', () => {
            socket.emit('stop_stream');
            startBtn.disabled = false;
            stopBtn.disabled = true;
        });

        socket.on('video_feed', function(data) {
            videoFeed.src = data.image;
            liveResults.innerHTML = '<h2>Live Results:</h2>';
            liveResults.innerHTML += '<table><thead><tr><th>Roll Number</th><th>Current Option</th><th>Distance (m)</th></tr></thead><tbody>';
            
            data.results.forEach(result => {
                if (!detectedStudents[result.name]) {
                    detectedStudents[result.name] = { first: result.code, last: result.code };
                } else {
                    detectedStudents[result.name].last = result.code;
                }
                liveResults.innerHTML += `<tr><td>${result.name}</td><td>${result.code}</td><td>${result.distance.toFixed(2)}</td></tr>`;
            });
            liveResults.innerHTML += '</tbody></table>';
            
            uniqueCount.textContent = `Detected: ${Object.keys(detectedStudents).length}/${data.total_count}`;
            
            // Update recognized names
            recognizedNames.innerHTML = '<strong>Recognized:</strong><br>' + 
                Object.keys(detectedStudents).join('<br>');
        });

        socket.on('final_results', function(data) {
            finalResults.innerHTML = '<h2>Final Results:</h2>';
            finalResults.innerHTML += '<table><thead><tr><th>Roll Number</th><th>First Option</th><th>Last Option</th></tr></thead><tbody>';
            Object.entries(data).forEach(([name, options]) => {
                finalResults.innerHTML += `<tr><td>${name}</td>&nbsp;&nbsp&nbsp;&nbsp<td>${options.first}</td>&nbsp;&nbsp&nbsp;&nbsp<td>${options.last}</td></tr><br>`;
            });
            finalResults.innerHTML += '</tbody></table>';
        });
    </script>
</body>
</html>
"""
def video_stream():
    global stream_active, detected_students, student_count
    logger.info("Starting video stream")
    cap = cv2.VideoCapture(0)  # Use 0 for default camera, or specify IP camera URL
    if not cap.isOpened():
        logger.warning("Default camera (0) not available, trying camera 1")
        cap = cv2.VideoCapture(1)
        
        # If camera 1 also fails, handle the failure
        if not cap.isOpened():
            logger.error("Failed to open any camera")
            return
    
    # If a camera stream is successfully opened
    logger.info("Camera stream opened successfully")
    
    # Your processing code here, e.g., reading frames

    frame_count = 0
    while stream_active:
        ret, frame = cap.read()
        if not ret:
            logger.warning(f"Failed to read frame {frame_count}")
            continue
        
        frame_count += 1
        if frame_count % 30 == 0:  # Log every 30 frames
            logger.debug(f"Processed {frame_count} frames")
        
        try:
            # Log the student_count before calling process_frame
            logger.debug(f"Calling process_frame with student_count: {student_count}")
            results, annotated_frame = process_frame(frame, student_count)
            logger.debug(f"Received results: {results}")
            #for reducing the redundandacy and applyng limt
            for result in results:
                if result['name'] not in detected_students:
                    if len(detected_students) < student_count:
                        detected_students[result['name']] = {'first': result['code'], 'last': result['code']}
                elif result['name'] in detected_students:
                    detected_students[result['name']]['last'] = result['code']

            encoded_frame = encode_frame(annotated_frame)
            socketio.emit('video_feed', {
                'image': encoded_frame, 
                'results': results,
                'unique_count': len(detected_students),
                'total_count': student_count
            })
        except Exception as e:
            logger.error(f"Error processing frame {frame_count}: {str(e)}")
            logger.exception("Detailed error information:")  # This will log the full traceback
        
        socketio.sleep(0.03)  # Adjust this value to control frame rate

    logger.info("Video stream ended")
    cap.release()


@app.route('/')
def index():
    return render_template_string(html_template)

@socketio.on('connect')
def handle_connect():
    logger.info('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    logger.info('Client disconnected')

@socketio.on('start_stream')
def handle_start_stream(data):
    global student_count, stream_active, detected_students
    student_count = int(data['count'])
    logger.info(f"Starting stream with student_count: {student_count}")
    detected_students = OrderedDict()
    stream_active = True
    socketio.start_background_task(video_stream)

@socketio.on('stop_stream')
def handle_stop_stream():
    global stream_active
    stream_active = False
    socketio.emit('final_results', dict(detected_students))

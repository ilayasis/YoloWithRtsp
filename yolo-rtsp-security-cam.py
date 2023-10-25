# Copyright (c) 2023, Phazer Tech
# All rights reserved.

# View the GNU AFFERO license found in the
# LICENSE file in the root directory.
import socketio
import json
import requests
import socket
import time
import os
import cv2
import queue
import threading
import numpy as np
from datetime import datetime
from ffmpeg import FFmpeg
from skimage.metrics import mean_squared_error as ssim
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter, BooleanOptionalAction
from sshkeyboard import listen_keyboard, stop_listening
import torch
#

# Parse command line arguments
parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
parser.add_argument("--stream", type=str, help="RTSP address of video stream.")
parser.add_argument('--monitor', default=False, action=BooleanOptionalAction, help="View the live stream. If no monitor is connected then leave this disabled (no Raspberry Pi SSH sessions).")
parser.add_argument("--yolo", type=str, help="Enables YOLO object detection. Enter a comma separated list of objects you'd like the program to record. The list can be found in the coco.names file")
parser.add_argument("--model", default='yolov8n', type=str, help="Specify which model size you want to run. Default is the nano model.")
parser.add_argument("--threshold", default=350, type=int, choices=range(1,10000), help="Determines the amount of motion required to start recording. Higher values decrease sensitivity to help reduce false positives. Default 350, max 10000.")
parser.add_argument("--start_frames", default=3, type=int, choices=range(1,30), help="Number of consecutive frames with motion required to start recording. Raising this might help if there's too many false positive recordings, especially with a high frame rate stream of 60 FPS. Default 3, max 30.")
parser.add_argument("--tail_length", default=8, type=int, choices=range(1,30), help="Number of seconds without motion required to stop recording. Raise this value if recordings are stopping too early. Default 8, max 30.")
parser.add_argument("--auto_delete", default=False, action=BooleanOptionalAction, help="Enables auto-delete feature. Recordings that have total length equal to the tail_length value (seconds) are assumed to be false positives and are auto-deleted.")
parser.add_argument('--testing', default=False, action=BooleanOptionalAction, help="Testing mode disables recordings and prints out the motion value for each frame if greater than threshold. Helps fine tune the threshold value.")
parser.add_argument('--frame_click', default=False, action=BooleanOptionalAction, help="Allows user to advance frames one by one by pressing any key. For use with testing mode on video files, not live streams, so set a video file instead of an RTSP address for the --stream argument.")
args = vars(parser.parse_args())
#
token ='Bearer eyJhbGcioiJIUzIINiIsInR5cCI6IkpXVCJ9.ey]pc3Mi0iJwYW5@aGVyliwiyxVkIjoicGFudGhlci1jbGlIbnRzIiwic3Viligia FsaxThIiwiaNFOIjoxNTc2MDcNDE2LCJleHAiOIEINZYMT020DZ9.aGtp4_eRUYrX7KWBHq01D1y2vnOVA6TarMaCzzTFckY'
headers = {"Authorization": token}
HTTP_POST = 'http://192.168.1.15:3002/droneDetected'

# sio = socketio.Client()
IP_RTSP = 'rtsp://192.168.1.5:554/profile2/media.smp'
rtsp_stream = args["stream"]
monitor = args["monitor"]
thresh = args["threshold"]
start_frames = args["start_frames"]
tail_length = args["tail_length"]
auto_delete = args["auto_delete"]
testing = args["testing"]
frame_click = args["frame_click"]
if frame_click:
    testing = True
    monitor = True
    print("frame_click enabled. Press any key to advance the frame by one, or hold down the key to advance faster. Make sure the video window is selected, not the terminal, when advancing frames.")
if args["yolo"]:
    yolo_list = [s.strip() for s in args["yolo"].split(",")]
    yolo_on = True
else:
    yolo_on = False

# Connect to Socket.IO server
# try:
#     sio.connect('http://192.168.1.42:5000')
# except socketio.exceptions.ConnectionError as e:
#     print(f'Failed to connect: {str(e)}')

# Set up variables for YOLO detection
if yolo_on:
    from ultralytics import YOLO
    stop_error = False

    CONFIDENCE = 0.5
    font_scale = 1
    thickness = 1
    labels = open("coco.names").read().strip().split("\n")
    colors = np.random.randint(0, 255, size=(len(labels), 3), dtype="uint8")

    #if u have cuda

    # device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    # print(f'Running on {device} device')
    #model = YOLO('/home/benjamin/PycharmProjects/yerida/runs/detect/train2/weights/best.pt').to(device)

    #else :

    torch.cuda.is_available = lambda: False
    model = YOLO('/home/benjamin/PycharmProjects/yerida/runs/detect/train2/weights/best.pt')

    # Check if the user provided list has valid objects
    for coconame in yolo_list:
        if coconame not in labels:
            print("Error! '"+coconame+"' not found in coco.names")
            stop_error = True
    if stop_error:
        exit("Exiting")

# Set up other internal variables
loop = True
cap = cv2.VideoCapture(rtsp_stream)
fps = cap.get(cv2.CAP_PROP_FPS)
period = 1/fps
tail_length = tail_length*fps
recording = False
ffmpeg_copy = 0
activity_count = 0
yolo_count = 0
ret, img = cap.read()
if img.shape[1]/img.shape[0] > 1.55:
    res = (256,144)
else:
    res = (216,162)
blank = np.zeros((res[1],res[0]), np.uint8)
resized_frame = cv2.resize(img, res)
gray_frame = cv2.cvtColor(resized_frame,cv2.COLOR_BGR2GRAY)
old_frame = cv2.GaussianBlur(gray_frame, (5,5), 0)
if monitor:
    cv2.namedWindow(rtsp_stream, cv2.WINDOW_NORMAL)

q = queue.Queue()
# Thread for receiving the stream's frames so they can be processed
def receive_frames():
    if cap.isOpened():
        ret, frame = cap.read()
    while ret and loop:
        if cap.isOpened():
            ret, frame = cap.read()
            if ret:
                q.put(frame)

# Record the stream when object is detected
def start_ffmpeg():
    try:
        ffmpeg_copy.execute()
    except:
        print("Issue recording the stream. Trying again.")
        time.sleep(1)
        ffmpeg_copy.execute()

# Functions for detecting key presses
def press(key):
    global loop
    if key == 'q':
        loop = False

def input_keyboard():
    listen_keyboard(
        on_press=press,
    )

def timer():
    delay = False
    period = 1
    now = datetime.now()
    now_time = now.time()
    start1 = now_time.replace(hour=0, minute=0, second=0, microsecond=0)
    start2 = now_time.replace(hour=0, minute=0, second=1, microsecond=10000)
    start_t=time.time()
    while loop:
        now = datetime.now()
        now_time = now.time()
        if(now_time>=start1 and now_time<=start2):
            day_num = now.weekday()
            if day_num == 0: print("Monday "+now.strftime('%m-%d-%Y'))
            elif day_num == 1: print("Tuesday "+now.strftime('%m-%d-%Y'))
            elif day_num == 2: print("Wednesday "+now.strftime('%m-%d-%Y'))
            elif day_num == 3: print("Thursday "+now.strftime('%m-%d-%Y'))
            elif day_num == 4: print("Friday "+now.strftime('%m-%d-%Y'))
            elif day_num == 5: print("Saturday "+now.strftime('%m-%d-%Y'))
            elif day_num == 6: print("Sunday "+now.strftime('%m-%d-%Y'))
            delay = True
        time.sleep(period - ((time.time() - start_t) % period))
        if delay:
            delay = False
            time.sleep(period - ((time.time() - start_t) % period))

# Process YOLO object detection
# @sio.on('connect')
def process_yolo():
    global img
    height, width, channels = img.shape
    detection_data = []
    detection_data = {'xmin': None, 'ymin': None, 'xmax': None,'ymax': None,'center_x': None,'center_y': None,'height': None,'width': None,'channels': None,'confidence': None,'class_id': None,'label': None,'ip': None}
    # s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # s.connect(('SERVER_IP_ADDRESS', SERVER_PORT))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # s.connect(('http://localhost', 3000))
    results = model.predict(img, conf=CONFIDENCE, verbose=False)[0]
    object_found = False

    # Loop over the detections
    for data in results.boxes.data.tolist():
        # Get the bounding box coordinates, confidence, and class id
        xmin, ymin, xmax, ymax, confidence, class_id = data

        # Converting the coordinates and the class id to integers
        xmin = int(xmin)
        ymin = int(ymin)
        xmax = int(xmax)
        ymax = int(ymax)
        center_x = int((xmin + xmax) / 2)
        center_y = int((ymin + ymax) / 2)
        class_id = int(class_id)

        print(class_id)
        if labels[class_id] in yolo_list:
            object_found = True
            detection_data['xmin'] = xmin
            detection_data['ymin'] = ymin
            detection_data['xmax'] = xmax
            detection_data['ymax'] = ymax
            detection_data['center_x'] = center_x
            detection_data['center_y'] = center_y
            detection_data['height'] = xmax
            detection_data['width'] = ymax
            detection_data['channels'] = channels
            detection_data['confidence'] = round(float(confidence), 2),
            detection_data['class_id'] = class_id
            detection_data['label'] = labels[class_id]
            detection_data['ip'] = IP_RTSP

        json_data = json.dumps(detection_data)
        # json_str = json.dumps(detection_data)
        print(json_data)
        # json_data = json.dumps(dictonary)
        # sio.emit('your_event_name', json_data)

        response = requests.post(HTTP_POST, json=json_data, headers=headers)
        print(response.json())
        # Draw a bounding box rectangle and label on the image
        color = [int(c) for c in colors[class_id]]
        cv2.rectangle(img, (xmin, ymin), (xmax, ymax), color=color, thickness=thickness)
        text = f"{labels[class_id]}: {confidence:.2f}"
        # Calculate text width & height to draw the transparent boxes as background of the text
        (text_width, text_height) = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fontScale=font_scale, thickness=thickness)[0]
        text_offset_x = xmin
        text_offset_y = ymin - 5
        box_coords = ((text_offset_x, text_offset_y), (text_offset_x + text_width + 2, text_offset_y - text_height))
        overlay = img.copy()
        cv2.rectangle(overlay, box_coords[0], box_coords[1], color=color, thickness=cv2.FILLED)
        # Add opacity (transparency to the box)
        img = cv2.addWeighted(overlay, 0.6, img, 0.4, 0)
        # Now put the text (label: confidence %)
        cv2.putText(img, text, (xmin, ymin - 5), cv2.FONT_HERSHEY_SIMPLEX,
            fontScale=font_scale, color=(0, 0, 0), thickness=thickness)
    return object_found


# Start the background threads
receive_thread = threading.Thread(target=receive_frames)
receive_thread.start()
keyboard_thread = threading.Thread(target=input_keyboard)
keyboard_thread.start()
timer_thread = threading.Thread(target=timer)
timer_thread.start()

# Main loop
while loop:
    if q.empty() != True:
        img = q.get()

        # Resize image, make it grayscale, then blur it
        resized_frame = cv2.resize(img, res)
        gray_frame = cv2.cvtColor(resized_frame,cv2.COLOR_BGR2GRAY)
        final_frame = cv2.GaussianBlur(gray_frame, (5,5), 0)

        # Calculate difference between current and previous frame, then get ssim value
        diff = cv2.absdiff(final_frame, old_frame)
        result = cv2.threshold(diff, 5, 255, cv2.THRESH_BINARY)[1]
        ssim_val = int(ssim(result,blank))
        old_frame = final_frame

        # Print value for testing mode
        if testing and ssim_val > thresh:
            print("motion: "+ str(ssim_val))

        # Count the number of frames where the ssim value exceeds the threshold value.
        # If the number of these frames exceeds start_frames value, run YOLO detection.
        # Start recording if an object from the user provided list is detected
        if not recording:
            if ssim_val > thresh:
                activity_count += 1
                if activity_count >= start_frames:
                    if yolo_on:
                        if process_yolo():
                            yolo_count += 1
                        else:
                            yolo_count = 0
                    if not yolo_on or yolo_count > 1:
                        filedate = datetime.now().strftime('%H-%M-%S')
                        if not testing:
                            folderdate = datetime.now().strftime('%Y-%m-%d')
                            if not os.path.isdir(folderdate):
                                os.mkdir(folderdate)
                            filename = '%s/%s.mkv' % (folderdate,filedate)
                            ffmpeg_copy = (
                                FFmpeg()
                                .option("y")
                                .input(
                                    rtsp_stream,
                                    rtsp_transport="tcp",
                                    rtsp_flags="prefer_tcp",
                                )
                                .output(filename, vcodec="copy", acodec="copy")
                            )
                            ffmpeg_thread = threading.Thread(target=start_ffmpeg)
                            ffmpeg_thread.start()
                            print(filedate + " recording started")
                        else:
                            print(filedate + " recording started - Testing mode")
                        recording = True
                        activity_count = 0
                        yolo_count = 0
            else:
                activity_count = 0
                yolo_count = 0

        # If already recording, count the number of frames where there's no motion activity
        # or no object detected and stop recording if it exceeds the tail_length value
        else:
            if yolo_on and not process_yolo() or not yolo_on and ssim_val < thresh:
                activity_count += 1
                if activity_count >= tail_length:
                    filedate = datetime.now().strftime('%H-%M-%S')
                    if not testing:
                        ffmpeg_copy.terminate()
                        ffmpeg_thread.join()
                        ffmpeg_copy = 0
                        print(filedate + " recording stopped")
                        # If auto_delete argument was provided, delete recording if total
                        # length is equal to the tail_length value, indicating a false positive
                        if auto_delete:
                            recorded_file = cv2.VideoCapture(filename)
                            recorded_frames = recorded_file.get(cv2.CAP_PROP_FRAME_COUNT)
                            if recorded_frames < tail_length + (fps/2) and os.path.isfile(filename):
                                os.remove(filename)
                                print(filename + " was auto-deleted")
                    else:
                        print(filedate + " recording stopped - Testing mode")
                    recording = False
                    activity_count = 0
            else:
                activity_count = 0

        # Monitor the stream
        if monitor:
            cv2.imshow(rtsp_stream, img)
            if frame_click:
                cv_key = cv2.waitKey(0) & 0xFF
                if cv_key == ord("q"):
                    loop = False
                if cv_key == ord("n"):
                    continue
            else:
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    loop = False
    else:
        time.sleep(period/2)

# Gracefully end threads and exit
stop_listening()
if ffmpeg_copy:
    ffmpeg_copy.terminate()
    ffmpeg_thread.join()
receive_thread.join()
keyboard_thread.join()
timer_thread.join()
cv2.destroyAllWindows()
print("Exiting")

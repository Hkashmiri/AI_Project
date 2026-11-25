FOCUS-SENSE
Multimodal monitoring of fatigue, cognitive workload and stress for human safety
FOCUS-SENSE watches for signs that a worker is too tired, overloaded or stressed to work safely. It is aimed at people who work alone with complex equipment and no supervisor nearby, such as astronauts or submarine crews. It supports the worker's own judgment; it is not a medical or safety-certified system.

Research question: can machine learning models using webcam facial features, EEG brainwave activity and skin conductance (GSR) detect fatigue, cognitive workload and stress more accurately than fixed threshold rules, on people they have never seen?

It uses three Random Forest models, one per signal, each trained on a different public dataset:

Model	Sensor	Dataset	Predicts	File
Fatigue	Webcam	UTA-RLDD (24 videos, 12 people)	alert / tired	models/model.pkl
Workload	EEG headset	STEW (48 people, 14 channels, 128 Hz)	low / high workload	eeg_model.pkl
Stress	GSR wristband	WESAD (15 people)	baseline / stress	gsr_model.pkl
No public dataset records all three signals from the same people, so the models are trained separately. Combining them into one readiness alert (decision-level fusion) is the planned next step. Today, only the webcam model runs live in the app; EEG and GSR are shown on recorded data because no headset or wristband was available.

The desktop app also checks posture and screen distance with simple calibrated rules (no model needed).

Results
Every model was tested only on people it had never seen during training.

Model	Test method	Accuracy	Macro F1	Recall (at-risk state)
Webcam fatigue	Leave-one-person-out (12 people)	57% (rules: 52%)		56% tired (rules: 8%)
Webcam fatigue	2-person test split	64% (rules: 47%)	0.62 (rules: 0.36)	79% tired (rules: 5%)
EEG workload	5-fold, split by person (48 people)	70.2%	0.702	
GSR stress	Leave-one-person-out (15 people)	87.8%	0.866	
Chance level is 50% for EEG (balanced classes) and 64% for GSR (always guessing "baseline").
Webcam: the model catches far more tired chunks than the eyes-closed/yawn rule, but it raises many false alarms: on the 2-person test split it flagged 54% of alert chunks as tired. Early drowsiness is hard to see; human observers in the UTA-RLDD study averaged 57.8% [3]. Treat its alerts as prompts to check in, not verdicts.
EEG and GSR: accuracy varies a lot between people (EEG ranges from 16% to 97%). Features are rescaled against each person's own average (PER_PERSON_NORM = True), so real use would need a short calibration recording per user.
Charts in results/: confusion_matrices.png, feature_importance.png; in the project root: eeg_feature_importance.png, gsr_feature_importance.png.
Project Structure
focus_sense/
├── main.py                        ← desktop app (CustomTkinter GUI + camera thread)
├── vision_engine.py               ← webcam + MediaPipe: posture, distance, fatigue
├── fatigue_model.py               ← runs models/model.pkl live on webcam frames
├── extract_features.py            ← face features (used live and for training)
├── demo_predict.py                ← EEG and GSR models on recorded data
├── test_fatigue.py                ← backup: fatigue model on the webcam, no GUI
├── models/
│   ├── model.pkl                  ← trained webcam fatigue model
│   └── face_landmarker.task       ← MediaPipe face model (downloaded on first run)
├── eeg_model.pkl                  ← trained EEG workload model
├── gsr_model.pkl                  ← trained GSR stress model
├── features.csv                   ← webcam features (one row per 2-second chunk)
├── eeg_features.csv               ← EEG features (theta, alpha, beta power per channel)
├── gsr_features.csv               ← GSR features (mean, std, min, max, slope)
├── FOCUS_SENSE_Webcam_local.ipynb ← trains the webcam model locally from features.csv
├── FOCUS_SENSE_Webcam_RLDD.ipynb  ← Colab: videos → features.csv → model
├── FOCUS_SENSE_EEG_GSR.ipynb      ← Colab: EEG + GSR training
├── FOCUS_SENSE_EEG_v1.ipynb       ← earlier EEG experiment (predicts 1–9 rating; not used in the app)
├── results/ and results_*.csv     ← test results and charts
├── requirements.txt
└── README.md
The videos and raw datasets are not in this repository. They are only needed for training, not for running the app.

Setup
Install Python 3.10 or 3.11 and make sure you have a working webcam.
Create and activate a virtual environment:
python -m venv venv
source venv/bin/activate        # macOS / Linux
venv\Scripts\activate           # Windows
Install the packages:
pip install -r requirements.txt
On Apple Silicon, if mediapipe fails to install, try:
pip install mediapipe --extra-index-url https://google-ml-packages.appspot.com
The first time the app runs, it downloads MediaPipe's face_landmarker.task into the models/ folder, so you need to be online for that first run.

On macOS, allow camera access for Terminal (or VS Code) when asked, or in System Settings → Privacy & Security → Camera.

Run the live app (webcam)
python main.py
Step	Action
1	Click START. The camera opens and "LIVE" appears.
2	Sit upright at a comfortable distance and look at the camera.
3	Click CALIBRATE (or press C) and hold still for about 2 seconds.
4	The app now flags posture, distance and tiredness.
5	Use the SENSITIVITY slider in Settings to make alerts stricter or looser.
6	Click STOP when done.
The FATIGUE (ML) tile shows the webcam model's answer every 2 seconds. A "YOU LOOK TIRED" alert fires only after about 6 seconds of "tired" in a row, so one wrong guess doesn't set it off. To demo it, close your eyes for a few seconds or yawn.

Status meanings
Status	Meaning
OPTIMAL	Posture and distance are within your calibrated range
WARNING	Small change from your baseline. Adjust your position.
CRITICAL	Large change. Banner, sound and notification fire.
NO FACE	The camera can't see your face. Check lighting and angle.
INACTIVE	The camera is off
Fatigue tile	Meaning
ALERT xx%	Model predicts alert, with its confidence
TIRED xx%	Model predicts tired, with its confidence
…	Collecting the first 2 seconds
NO MODEL	models/model.pkl was not found or could not be loaded
Run the EEG and GSR demo
These models are shown on recorded data. The script leaves the chosen person out of training, trains on everyone else, then shows its predictions next to the true labels.

python demo_predict.py                          # both models
python demo_predict.py --model gsr --person S5  # one model, one person
python demo_predict.py --list                   # list person IDs
Retraining
Fatigue (local, recommended): open FOCUS_SENSE_Webcam_local.ipynb from the project folder. It reads features.csv, compares the model with the rules, and saves models/model.pkl plus charts and scores in results/. No Google Drive needed.
Fatigue from videos: FOCUS_SENSE_Webcam_RLDD.ipynb in Google Colab sorts the UTA-RLDD videos (12 people, alert 0 and drowsy 10 videos), runs extract_features.py, then trains the model. It uses 14 eye, mouth and head-tilt features. Position features are left out because the camera moved between the alert and drowsy recordings.
EEG + GSR: FOCUS_SENSE_EEG_GSR.ipynb in Google Colab reads the STEW and WESAD zips from Google Drive (My Drive/FOCUS-SENSE/eeg and /gsr).
STEW: https://ieee-dataport.org/open-access/stew-simultaneous-task-eeg-workload-dataset
WESAD: https://ubi29.informatik.uni-siegen.de/usi/data_wesad.html
The .pkl models must be loaded with the same scikit-learn version they were trained with. requirements.txt pins it; if you retrain with a different version, update the pin.

Troubleshooting
Problem	Fix
"Could not open webcam"	Close other apps using the camera (Zoom, Teams, etc.)
FATIGUE tile shows "NO MODEL"	Put model.pkl in the models/ folder
Error loading a .pkl	Install the scikit-learn version listed in requirements.txt
MediaPipe import error	pip install --upgrade mediapipe
face_landmarker.task download fails	Download it from the MediaPipe link in vision_engine.py into models/
Notifications not appearing	Allow notifications for Python / Terminal in OS settings
Too many posture alerts	Lower the Sensitivity slider
Limitations and responsible use
Accuracy varies between people, sensors and recording conditions.
The three models come from separate datasets, so combined accuracy has not been tested.
The app processes face images on the device. Use it only with informed consent, and to support the worker, not to monitor or discipline them.
References
L. K. Barger et al., "Prevalence of sleep deficiency and use of hypnotic drugs in astronauts before, during, and after spaceflight," The Lancet Neurology, 2014.
T. Soukupová and J. Čech, "Real-Time Eye Blink Detection using Facial Landmarks," CVWW, 2016.
R. Ghoddoosian, M. Galib and V. Athitsos, "A Realistic Dataset and Baseline Temporal Model for Early Drowsiness Detection," CVPR Workshops, 2019.
"STEW: Simultaneous Task EEG Workload Dataset," IEEE DataPort, 2018, doi:10.21227/44r8-ya50.
P. Schmidt et al., "Introducing WESAD, a Multimodal Dataset for Wearable Stress and Affect Detection," ICMI 2018, doi:10.1145/3242969.3242985.
Team
Tolu Adeola, Noah Panto, Hafsah Kashmiri, Anita Lamai

Built with OpenCV · MediaPipe · scikit-learn · CustomTkinter · Python 3
# FOCUS-SENSE
### Posture & Eye-Strain Prevention Tool

Ultra-minimalist desktop app that uses your webcam + MediaPipe Face Mesh
to monitor screen distance and posture in real time.

---

## Project Structure

```
focus_sense/
├── main.py            ← GUI (CustomTkinter) + threading logic
├── vision_engine.py   ← OpenCV + MediaPipe detection engine
├── requirements.txt   ← Python dependencies
└── README.md
```

---

## Setup & Run

### 1 — Prerequisites
- Python 3.10 or 3.11 recommended  
- A working webcam  

### 2 — Create a virtual environment (recommended)
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3 — Install dependencies
```bash
pip install -r requirements.txt
```

> **Note for macOS (Apple Silicon):** If `mediapipe` fails to install, try:
> ```bash
> pip install mediapipe --extra-index-url https://google-ml-packages.appspot.com
> ```

### 4 — Run the app
```bash
python main.py
```

---

## How to Use

| Step | Action |
|------|--------|
| 1 | Click **START** — the camera opens and "LIVE" appears |
| 2 | Sit in your ideal upright posture at a comfortable distance |
| 3 | Click **CALIBRATE** — hold still for ~2 seconds |
| 4 | The app now monitors deviations from your baseline |
| 5 | Adjust **SENSITIVITY** slider to make alerts stricter / more lenient |
| 6 | Click **STOP** to close the camera when done |

---

## Status Meanings

| Status | Meaning |
|--------|---------|
| **OPTIMAL** | Posture and distance are within your calibrated range |
| **WARNING** | Minor deviation — a gentle reminder to readjust |
| **CRITICAL** | Significant issue — system notification fired |
| **NO FACE** | Camera can't see your face; check lighting / angle |
| **INACTIVE** | Camera is off |

---

## Technical Notes

- **Distance** is measured by the pixel gap between inner-eye landmarks (33 & 263). A smaller gap means you are sitting closer to the screen.
- **Posture** is tracked by the Y-coordinate of nose landmark 1. Downward drift indicates slouching.
- **Focus Score** is a rolling average over the last 60 seconds (100 = perfect, 0 = worst).
- The camera loop runs on a **daemon thread**; the GUI thread never blocks.
- System notifications (via `plyer`) are throttled to once every 30 seconds.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Could not open webcam" | Close any other app using the camera (Zoom, Teams, etc.) |
| App freezes on START | Ensure `plyer` is installed; run `pip install plyer` |
| MediaPipe import error | Upgrade: `pip install --upgrade mediapipe` |
| Notifications not appearing | Check OS notification permissions for Python / Terminal |
| False positives on posture | Lower the Sensitivity slider |

---

*Built with OpenCV · MediaPipe · CustomTkinter · Python 3*


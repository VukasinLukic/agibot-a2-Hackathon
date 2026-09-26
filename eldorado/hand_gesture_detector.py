"""
Hand Gesture Detector
----------------------
Opens the Mac's webcam and detects hands + hand landmarks in real time
using MediaPipe. This is the shared building block (HandGestureDetector)
used by collect_gestures.py and detect_gestures.py.

Run this file directly for a plain hand-tracking demo with no gesture
matching. For the full pipeline see:
  - collect_gestures.py  (record labeled gestures into a database)
  - detect_gestures.py   (recognize gestures live and print the word)

Requirements:
    pip install opencv-python mediapipe

Run:
    python hand_gesture_detector.py

Press 'q' to quit.
"""

import cv2
import mediapipe as mp


class HandGestureDetector:
    def __init__(self, max_hands: int = 2, detection_confidence: float = 0.7,
                 tracking_confidence: float = 0.7):
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            max_num_hands=max_hands,
            min_detection_confidence=detection_confidence,
            min_tracking_confidence=tracking_confidence,
        )
        self.mp_drawing = mp.solutions.drawing_utils
        self.mp_styles = mp.solutions.drawing_styles

    def find_hands(self, frame):
        """Detect hands in a BGR frame. Returns the MediaPipe results object."""
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return self.hands.process(rgb_frame)

    def draw_landmarks(self, frame, results):
        """Draw hand landmarks + connections on the frame in place."""
        if results.multi_hand_landmarks:
            for hand_landmarks in results.multi_hand_landmarks:
                self.mp_drawing.draw_landmarks(
                    frame,
                    hand_landmarks,
                    self.mp_hands.HAND_CONNECTIONS,
                    self.mp_styles.get_default_hand_landmarks_style(),
                    self.mp_styles.get_default_hand_connections_style(),
                )

    def close(self):
        self.hands.close()


def main():
    cap = cv2.VideoCapture(0)  # 0 = default camera (built-in MacBook webcam)

    if not cap.isOpened():
        print("Error: could not access the camera. Check macOS camera permissions "
              "for your terminal/IDE in System Settings > Privacy & Security > Camera.")
        return

    detector = HandGestureDetector()

    print("Camera started. Press 'q' to quit.")

    while True:
        success, frame = cap.read()
        if not success:
            print("Error: failed to read frame from camera.")
            break

        frame = cv2.flip(frame, 1)  # mirror image, feels more natural
        results = detector.find_hands(frame)
        detector.draw_landmarks(frame, results)

        cv2.imshow("Hand Gesture Detector", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    detector.close()


if __name__ == "__main__":
    main()
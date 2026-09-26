import cv2


def draw_person(frame, person, locked=False):
    x1, y1, x2, y2 = person["bbox"]

    if locked:
        color = (0, 0, 255)
        label = f"LOCKED ID {person['track_id']}"
    elif person.get("face_confirmed"):
        color = (255, 0, 255)
        label = f"FACE OK ID {person['track_id']}"
    elif person["is_close"]:
        color = (0, 255, 255)
        label = f"CLOSE ID {person['track_id']}"
    else:
        color = (0, 255, 0)
        label = f"ID {person['track_id']}"

    label += (
        f" {person['confidence']:.2f}"
        f" h:{person['height_ratio']:.2f}"
        f" area:{person['area_ratio']:.2f}"
    )
    label += f" h:{person['height_ratio']:.2f}"
    
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

    cv2.putText(
        frame,
        label,
        (x1, max(25, y1 - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        color,
        2,
    )


def draw_status(frame, locked_track_id):
    text = f"Locked ID: {locked_track_id}" if locked_track_id is not None else "No lock"

    cv2.putText(
        frame,
        text,
        (20, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (255, 255, 255),
        2,
    )


def draw_debug_overlay(frame, lines):
    y = 70
    for line in lines:
        cv2.putText(
            frame,
            line,
            (20, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
        )
        y += 22

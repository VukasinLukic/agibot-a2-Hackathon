def add_person_geometry(person, frame_shape):
    h, w = frame_shape[:2]

    x1, y1, x2, y2 = person["bbox"]

    box_w = x2 - x1
    box_h = y2 - y1

    cx = int((x1 + x2) / 2)
    cy = int((y1 + y2) / 2)

    height_ratio = box_h / h
    area_ratio = (box_w * box_h) / (w * h)
    center_x_ratio = cx / w
    center_y_ratio = cy / h

    person["center"] = (cx, cy)
    person["height_ratio"] = height_ratio
    person["area_ratio"] = area_ratio
    person["center_x_ratio"] = center_x_ratio
    person["center_y_ratio"] = center_y_ratio

    return person


def mark_close_persons(persons, close_height_ratio):
    for person in persons:
        person["is_close"] = person["height_ratio"] >= close_height_ratio

    return persons


def select_closest_person(persons):
    close_people = [p for p in persons if p["is_close"]]

    if not close_people:
        return None

    # largest bbox height = closest proxy
    return max(close_people, key=lambda p: p["height_ratio"])

class LockManager:
    def __init__(
        self,
        max_lost_frames=15,
        stability_frames=5,
        stability_tolerance=0.05,
        x_stability_tolerance=0.02,
    ):
        self.locked_track_id = None
        self.lost_frames = 0  # consecutive frames the locked person was not detected
        self.max_lost_frames = max_lost_frames
        self._stability_frames = stability_frames
        self._stability_tolerance = stability_tolerance
        self._x_stability_tolerance = x_stability_tolerance
        self._candidate_track_id = None
        self._candidate_stable_count = 0
        self._candidate_last_ratio = None
        self._candidate_start_x_ratio = None

    @property
    def candidate_stable_count(self):
        return self._candidate_stable_count

    def reset(self):
        self.locked_track_id = None
        self.lost_frames = 0
        self._reset_candidate()

    def _candidate_x_ratio(self, candidate_person):
        if "center_x_ratio" in candidate_person:
            return candidate_person["center_x_ratio"]

        center = candidate_person.get("center")
        frame_width = candidate_person.get("frame_width")
        if center is None or frame_width in (None, 0):
            return None

        return center[0] / frame_width

    def _reset_candidate(self):
        self._candidate_track_id = None
        self._candidate_stable_count = 0
        self._candidate_last_ratio = None
        self._candidate_start_x_ratio = None

    def update(self, persons, candidate_person=None):
        active_ids = [p["track_id"] for p in persons]

        # if we dont have a lock try to acquire it with the candidate person, if any
        if self.locked_track_id is None:
            if candidate_person is not None:
                track_id = candidate_person["track_id"]
                ratio = candidate_person["height_ratio"]
                x_ratio = self._candidate_x_ratio(candidate_person)

                # if we have a new candidate, reset stability count
                if track_id != self._candidate_track_id:
                    self._candidate_track_id = track_id
                    self._candidate_stable_count = 1
                    self._candidate_last_ratio = ratio
                    self._candidate_start_x_ratio = x_ratio
                else:
                    height_is_stable = abs(ratio - self._candidate_last_ratio) <= self._stability_tolerance
                    x_is_stable = (
                        x_ratio is not None
                        and self._candidate_start_x_ratio is not None
                        and abs(x_ratio - self._candidate_start_x_ratio) <= self._x_stability_tolerance
                    )

                    # only count frames where the person is stable in distance and horizontal position
                    if height_is_stable and x_is_stable:
                        self._candidate_stable_count += 1
                    else:
                        self._candidate_stable_count = 1
                        self._candidate_start_x_ratio = x_ratio

                    # update the height value used for the next stability check
                    self._candidate_last_ratio = ratio

                # if the candidate is stable for enough frames, lock it
                if self._candidate_stable_count >= self._stability_frames:
                    self.locked_track_id = track_id
                    self.lost_frames = 0
                    self._reset_candidate()
            else:
                self._reset_candidate()
            
            # if we don't have a lock yet, return None
            return self.locked_track_id

        # if we have a lock, check if it's still active
        if self.locked_track_id in active_ids:
            self.lost_frames = 0
            return self.locked_track_id

        self.lost_frames += 1

        if self.lost_frames > self.max_lost_frames:
            self.locked_track_id = None
            self.lost_frames = 0

        # return the currently locked track id, or None if we lost the lock
        return self.locked_track_id

    def is_locked(self, person):
        # a person is locked if their track_id matches the locked_track_id
        return person["track_id"] == self.locked_track_id

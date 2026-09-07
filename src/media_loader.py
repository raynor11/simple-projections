import cv2
from pathlib import Path


class MediaLoader:
    """Load and decode video/image media."""

    def __init__(self, media_path):
        self.media_path = Path(media_path)
        self.cap = None
        self.frame_index = 0
        self.total_frames = 0
        self.width = 0
        self.height = 0
        self.fps = 30
        self.is_video = False
        self._load_media()

    def _load_media(self):
        """Open media file (video or image)."""
        if not self.media_path.exists():
            raise FileNotFoundError(f"Media not found: {self.media_path}")

        suffix = self.media_path.suffix.lower()

        if suffix in ['.mp4', '.mov', '.avi', '.mkv', '.webm']:
            self.is_video = True
            self.cap = cv2.VideoCapture(str(self.media_path))
            if not self.cap.isOpened():
                raise RuntimeError(f"Failed to open video: {self.media_path}")

            self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30

        elif suffix in ['.png', '.jpg', '.jpeg', '.bmp', '.webp']:
            img = cv2.imread(str(self.media_path))
            if img is None:
                raise RuntimeError(f"Failed to load image: {self.media_path}")

            self.is_video = False
            self.height, self.width = img.shape[:2]
            self.total_frames = 1
            self.fps = 30
        else:
            raise ValueError(f"Unsupported media format: {suffix}")

    def get_next_frame(self):
        """
        Get next frame as BGR numpy array, looping on video end.
        Returns (frame, width, height) or None on error.
        """
        if self.is_video:
            ret, frame = self.cap.read()
            if not ret:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self.cap.read()

            if not ret:
                return None

            self.frame_index = (self.frame_index + 1) % self.total_frames
            return frame, self.width, self.height
        else:
            img = cv2.imread(str(self.media_path))
            return img, self.width, self.height

    def seek_frame(self, frame_num):
        """Seek to specific frame (video only)."""
        if self.is_video and self.cap:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            self.frame_index = frame_num

    def reset(self):
        """Reset to frame 0."""
        self.frame_index = 0
        if self.is_video and self.cap:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    def close(self):
        """Release resources."""
        if self.cap:
            self.cap.release()

    def __del__(self):
        self.close()

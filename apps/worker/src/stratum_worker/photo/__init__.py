"""Photos, read and framed: where the faces and the subject are, and which part to show.

- faces.py: YuNet face detector (MIT), run with onnxruntime.
- read.py: one measurement per photo - faces, salience, busyness - cached by file.
- frame.py: per size, the crop that keeps faces and the subject whole and out from
  under the words, with the words over the calmest part of the photo.
"""

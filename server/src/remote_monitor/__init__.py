"""Remote Monitor - a self-written remote video monitoring server.

Pipeline: camera capture -> analytics (motion / tracking / faces) -> OSD -> H.264 encode
-> WebSocket fan-out -> browser (WebCodecs decode) or native viewer.
"""

__version__ = "1.0.0"

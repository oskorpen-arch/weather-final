"""
MOOD WEATHER - path relay (paste into a SECOND Python script component)

Why it exists: a Grasshopper Timer only re-runs what is DOWNSTREAM of it. Read
File would otherwise keep showing the first version of the JSON. Putting this tiny
relay between the Timer and Read File makes Read File re-read every tick.

INPUTS   folder   text   full path to the exports folder, e.g. C:/.../exports
         tick     any    wire the Timer here
OUTPUTS  all_path text   -> Read File #1
         room_path text  -> Read File #2
"""
import os

_folder = globals().get("folder")
if _folder:
    all_path = os.path.join(_folder, "_all_systems.json")
    room_path = os.path.join(_folder, "_room.json")
else:
    all_path = None
    room_path = None

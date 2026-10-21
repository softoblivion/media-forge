"""Exceptions shared by the core modules.

`UserError` carries one English sentence meant for the user; the shell shows it as is (BRIDGE_API.md 1).
`Cancelled` stops a job that the user cancelled; it never reaches the user as an error.
"""

from __future__ import annotations


class UserError(Exception):
    """An error whose message is a single neutral sentence for the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Cancelled(Exception):
    """The job was cancelled through its cancel event."""


# Messages used in more than one module.
MSG_BUSY = "The file is being processed."
MSG_UNKNOWN_ITEM = "The file is no longer in the list."
MSG_NOT_MEDIA = "Not a media file."
MSG_NOT_FOUND = "The file was not found."
MSG_NO_FFMPEG = "ffmpeg is not available."
MSG_NO_AUDIO = "The file has no audio track."
MSG_NO_VIDEO = "The file has no video track."
MSG_CANNOT_LIMIT = "This format cannot be limited in size."
MSG_LIMIT_NOT_MET = "The size limit could not be met; try a lower resolution."
MSG_LIMIT_TOO_SMALL_IMAGE = "The size limit is too small for this image."
MSG_LIMIT_NOT_MET_AUDIO = "The size limit could not be met at the lowest bitrate."
MSG_NO_DURATION = "The duration of the file is unknown, so the size limit cannot be applied."
MSG_OUTPUT_DIR_MISSING = "The output folder does not exist."
MSG_MODEL_DAMAGED = "The model file is damaged."
MSG_MODEL_DOWNLOAD = "The model could not be downloaded."
MSG_MODEL_MISSING = "The model file is missing."
MSG_DISK_FULL = "There is not enough space on the disk."
MSG_WRITE_DENIED = "The output file cannot be written; it may be open in another program."
MSG_DAMAGED_INPUT = "The file is damaged or uses an unsupported format."
MSG_UNSUPPORTED_CODEC = "The codec is not supported for this format."
MSG_INTERNAL = "The conversion failed; details are in the log."

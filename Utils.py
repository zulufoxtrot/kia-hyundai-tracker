import logging
import subprocess


def check_if_laptop_is_asleep():
    """
    It looks like the program resumes execution periodically while my macbook sleeps.
    This causes urllib to hang.
    To mitigate this problem, this function checks whether the laptop is awake.
    source: https://stackoverflow.com/questions/42635378/detect-whether-host-is-in-sleep-or-awake-state-in-macos
    """
    try:
        result = subprocess.run(['system_profiler', 'SPDisplaysDataType'], stdout=subprocess.PIPE)
    except FileNotFoundError:
        logging.debug("Can't check laptop status. Running on a non-mac device?")
        return False
    if "Display Asleep" in result.stdout.decode():
        return True
    else:
        return False

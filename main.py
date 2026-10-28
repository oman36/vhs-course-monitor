import logging
import os
import re
import time

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from threading import Lock

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class CodeStorage:
    """Stores course codes."""

    def __init__(self, filename="course_codes.txt"):
        self.filename = filename
        self._codes = self._load()

    def _load(self):
        """Loads saved course codes from a file."""
        if not os.path.exists(self.filename):
            return {}
        try:
            with open(self.filename, "r", encoding="utf-8") as f:
                codes = {}
                for line in f:
                    line = line.strip()
                    if line:
                        if "|" in line:
                            code, date = line.split("|")
                            codes[code] = date
                        else:
                            codes[line] = ""
                return codes
        except Exception as e:
            logger.error("Error loading course codes: %s", e)
            return {}

    def exists(self, code):
        """Checks whether a code exists."""
        return code in self._codes

    def add(self, code):
        """Adds a code to memory."""
        self._codes.setdefault(code, time.strftime("%d.%m.%Y %H:%M:%S"))

    def save(self):
        """Saves all codes to a file."""
        try:
            with open(self.filename, "w", encoding="utf-8") as f:
                for code, date in self._codes.items():
                    f.write(f"{code}|{date}\n")
            logger.info(
                "Saved %s course codes to %s", len(self._codes), self.filename
            )
        except Exception as e:
            logger.error("Error saving course codes: %s", e)


class RateLimiter:
    """Semaphore for limiting the message sending rate."""

    def __init__(self, is_group=False):
        """
        Initializes the rate limiter.

        Args:
            is_group: True for groups (10 messages per minute), False for private chats (1 message per second)
        """
        self._lock = Lock()
        self._is_group = is_group

        if self._is_group:
            self._max_messages_per_minute = 10
            self._window_seconds = 60.0
            self._message_timestamps = []
        else:
            self._min_interval = 1.0
            self._last_send_time = 0

    def acquire(self):
        """
        Blocks execution if necessary to comply with rate limits.
        Must be called before sending a message.
        """
        with self._lock:
            current_time = time.time()

            if self._is_group:
                # For groups: check a rolling one-minute window
                # Remove timestamps older than one minute
                cutoff_time = current_time - self._window_seconds
                self._message_timestamps = [
                    ts for ts in self._message_timestamps if ts > cutoff_time
                ]

                # Wait if the message limit for the last minute has been reached
                if len(self._message_timestamps) >= self._max_messages_per_minute:
                    # Wait until one minute has passed since the oldest message
                    oldest_timestamp = self._message_timestamps[0]
                    wait_time = self._window_seconds - (current_time - oldest_timestamp)
                    if wait_time > 0:
                        time.sleep(wait_time)
                        # Update the time after waiting
                        current_time = time.time()
                        # Remove old timestamps again
                        cutoff_time = current_time - self._window_seconds
                        self._message_timestamps = [
                            ts for ts in self._message_timestamps if ts > cutoff_time
                        ]
            else:
                # For private chats: a simple interval check
                time_since_last_send = current_time - self._last_send_time
                if time_since_last_send < self._min_interval:
                    sleep_time = self._min_interval - time_since_last_send
                    time.sleep(sleep_time)
                    current_time = time.time()

    def record(self):
        """
        Records that a message was sent.
        Must be called after a message is sent successfully.
        """
        with self._lock:
            send_time = time.time()
            if self._is_group:
                self._message_timestamps.append(send_time)
            else:
                self._last_send_time = send_time


class Telegram:
    """Sends Telegram messages with adaptive rate limiting."""

    def __init__(self, bot_token=None, chat_id=None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

        # Determine the chat type: a negative chat_id indicates a group
        try:
            chat_id_int = int(self.chat_id) if self.chat_id else 0
            is_group = chat_id_int < 0
        except (ValueError, TypeError):
            is_group = False

        # Create a rate limiter based on the chat type
        self._rate_limiter = RateLimiter(is_group=is_group)

    def send(self, message):
        """Sends a Telegram message while respecting rate limits."""
        if not self.bot_token or not self.chat_id:
            raise ValueError(
                "Telegram is not configured: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing"
            )

        # Use a semaphore to limit the sending rate
        self._rate_limiter.acquire()

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 400:
            logger.error("Failed to send a Telegram message: %s", response.text)
        response.raise_for_status()

        # Record that the message was sent
        self._rate_limiter.record()

        return response.json()


class Searcher:
    """Searches for courses on the VHS website."""

    def __init__(self):
        self.url_search = (
            "https://www.vhsit.berlin.de/VHSKURSE/BusinessPages/CourseSearch.aspx"
        )
        self.url_courselist = (
            "https://www.vhsit.berlin.de/VHSKURSE/BusinessPages/CourseList.aspx"
        )
        self.session = requests.Session()

    def _get_value(self, soup, name):
        """Extracts the value of an input element by name."""
        tag = soup.find("input", {"name": name})
        return tag["value"] if tag and "value" in tag.attrs else ""

    def _extract_course_code(self, course_data):
        """Extracts the course code from the course data (index 3)."""
        if len(course_data) > 3:
            code = course_data[3].strip()
            return code if code else None
        return None

    def search(self):
        """Yields the courses found."""
        # 1. Load the search page
        response = self.session.get(self.url_search)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        viewstate = self._get_value(soup, "__VIEWSTATE")
        eventvalidation = self._get_value(soup, "__EVENTVALIDATION")
        viewstategen = self._get_value(soup, "__VIEWSTATEGENERATOR")

        # 2. Activate the "Erweitert" tab (advanced search)
        logger.info("Activating the 'Erweitert' tab...")
        payload_tab = {
            "__VIEWSTATE": viewstate,
            "__VIEWSTATEGENERATOR": viewstategen,
            "__EVENTVALIDATION": eventvalidation,
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "ctl00$Content$lbtnTab2": "Erweitert",
            "ctl00$Content$SimpleSearch1$SimpleSearchBox$txtSearchTerm": "",
            "ctl00$Content$KeywordsList1$cmbKeyword": "-1",
            "ctl00$Content$AreaList1$cmbDistricts": "0",
            "ctl00$Content$AdvancedSearch1$SearchBox1$txtSearchTerm": "",
            "ctl00$Content$KeywordsListAdvanced1$cmbKeyword": "-1",
            "ctl00$Content$AreaListAdvanced1$CheckBoxListDistricts$0": "0",
            "ctl00$Content$LyceumSelection1$cmbLyceum": "0",
            "ctl00$Content$TimeDependingInput1$cmbTimeStructur": "0",
            "ctl00$Content$TimeDependingInput1$txtCourseInstructor": "",
            "ctl00$Content$TimeDependingInput1$txtBeginFrom": "",
            "ctl00$Content$TimeDependingInput1$txtEndTo": "",
            "ctl00$Content$CourseNumber1$searchBoxCourseNr$txtSearchTerm": "",
        }

        result_tab = self.session.post(self.url_search, data=payload_tab)
        result_tab.raise_for_status()
        soup_tab = BeautifulSoup(result_tab.text, "html.parser")
        with open("output_tab.html", "w") as f:
            f.write(result_tab.text)

        viewstate = self._get_value(soup_tab, "__VIEWSTATE")
        eventvalidation = self._get_value(soup_tab, "__EVENTVALIDATION")
        viewstategen = self._get_value(soup_tab, "__VIEWSTATEGENERATOR")
        logger.info("The 'Erweitert' tab is activated")

        # 3. Build the search parameters
        payload = {
            "__VIEWSTATE": viewstate,
            "__VIEWSTATEGENERATOR": viewstategen,
            "__EVENTVALIDATION": eventvalidation,
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "ctl00$Content$SimpleSearch1$SimpleSearchBox$txtSearchTerm": "",
            "ctl00$Content$KeywordsList1$cmbKeyword": "-1",
            "ctl00$Content$AreaList1$cmbDistricts": "0",
            "ctl00$Content$AdvancedSearch1$SearchBox1$txtSearchTerm": "",
            "ctl00$Content$KeywordsListAdvanced1$cmbKeyword": "2271",
            "ctl00$Content$KeywordsListAdvanced1$GridView1$ctl03$KeywordRelation": "OR",
            "ctl00$Content$AreaListAdvanced1$CheckBoxListDistricts$2": "32",
            "ctl00$Content$AreaListAdvanced1$CheckBoxListDistricts$3": "41",
            "ctl00$Content$AreaListAdvanced1$CheckBoxListDistricts$4": "40",
            "ctl00$Content$AreaListAdvanced1$CheckBoxListDistricts$5": "31",
            "ctl00$Content$AreaListAdvanced1$CheckBoxListDistricts$7": "33",
            "ctl00$Content$LyceumSelection1$cmbLyceum": "0",
            "ctl00$Content$TimeDependingInput1$cmbTimeStructur": "0",
            "ctl00$Content$TimeDependingInput1$txtCourseInstructor": "",
            "ctl00$Content$TimeDependingInput1$txtBeginFrom": time.strftime("%d.%m.%Y"),
            "ctl00$Content$TimeDependingInput1$txtEndTo": "",
            "ctl00$Content$TimeDependingInput1$btnSearch": "Suchen",
            "ctl00$Content$CourseNumber1$searchBoxCourseNr$txtSearchTerm": "",
        }

        # 4. Send a POST request with the advanced search parameters
        logger.info("Sending an advanced search request...")
        result = self.session.post(self.url_search, data=payload)
        result.raise_for_status()
        soup_result = BeautifulSoup(result.text, "html.parser")
        with open("output.html", "w") as f:
            f.write(result.text)

        # 5. Process all result pages
        current_soup = soup_result
        page_num = 1

        for page_num in range(1, 3000):
            # Extract courses from the current page
            page_courses = []
            for course in current_soup.select(
                "#ctl00_Content_ILDataGrid1 tr.DataGridItem, #ctl00_Content_ILDataGrid1 tr.DataGridAlternatingItem"
            ):
                tds = course.find_all("td")
                cols = [td.get_text(strip=True) for td in tds]

                # Find the link in the course title column
                link = ""
                for td in tds:
                    if "DataGridItemCourseTitle" in td.get("class", []):
                        a_tag = td.find("a", href=True)
                        if a_tag:
                            link = a_tag["href"]
                        break
                # Append the link as an additional column
                cols.append(link)

                if cols:
                    code = self._extract_course_code(cols)
                    if code:
                        page_courses.append(cols)
                        yield cols
            logger.info("Page %s: found %s courses", page_num, len(page_courses))

            # Check whether the next-page button exists and is enabled
            next_button = current_soup.find(
                "input", {"name": lambda x: x and "ctl04" in x and "ILDataGrid1" in x}
            )

            if not next_button:
                logger.warning("Next-page button not found")
                break

            button_class = next_button.get("class", [])
            if isinstance(button_class, str):
                button_class = button_class.split()

            if "aspNetDisabled" in button_class:
                logger.info("Reached the last page")
                break

            button_name = next_button.get("name")
            if not button_name:
                logger.error("Could not find the button name")
                break

            viewstate_current = self._get_value(current_soup, "__VIEWSTATE")
            eventvalidation_current = self._get_value(current_soup, "__EVENTVALIDATION")
            viewstategen_current = self._get_value(current_soup, "__VIEWSTATEGENERATOR")

            payload_next = {
                "__VIEWSTATE": viewstate_current,
                "__VIEWSTATEGENERATOR": viewstategen_current,
                "__EVENTVALIDATION": eventvalidation_current,
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                f"{button_name}.x": "12",
                f"{button_name}.y": "12",
            }

            result_next = self.session.post(self.url_courselist, data=payload_next)
            result_next.raise_for_status()
            with open(f"output_page{page_num}.html", "w") as f:
                f.write(result_next.text)
            current_soup = BeautifulSoup(result_next.text, "html.parser")


class Service:
    """Main service for processing courses."""

    def __init__(self, telegram: Telegram, storage: CodeStorage, chunk=5):
        self.chunk = chunk
        self.storage = storage
        self.telegram = telegram
        self.batch = []  # Batch of new courses to send
        self.total_found = 0
        self.total_new = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Saves pending items on exit."""
        self._save_batch()  # _save_batch() already saves codes to disk

    def _extract_course_code(self, course_data):
        """Extracts the course code from the course data (index 3)."""
        if len(course_data) > 3:
            code = course_data[3].strip()
            return code if code else None
        return None

    def _format_course_entry(self, course_data):
        """Formats a single course for inclusion in a batch message."""
        if len(course_data) < 5:
            return None

        district = course_data[2] if len(course_data) > 2 else "N/A"
        code = course_data[3] if len(course_data) > 3 else "N/A"
        name = course_data[4] if len(course_data) > 4 else "N/A"
        date_time = course_data[5] if len(course_data) > 5 else "N/A"
        places = course_data[6] if len(course_data) > 6 else "N/A"
        notes = course_data[7] if len(course_data) > 7 else ""
        price = course_data[8] if len(course_data) > 8 else "N/A"
        link = course_data[9] if len(course_data) > 9 else "N/A"

        # Remove line breaks from the date/time: \r\n
        date_time = date_time.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        name = re.sub(r"([ABC][123](\.[0123])?\+?)", r"【<b>\1</b>】", name)
        entry = (
            f"<u>{name}</u>\n\n"
            f"<i>Code:</i> {code}\n"
            f"<i>District:</i> {district}\n"
            f"<i>Date/Time:</i> {date_time}\n"
            f"<i>Places:</i> {places}\n"
            f"<i>Link:</i> https://www.vhsit.berlin.de/VHSKURSE/BusinessPages/{link}\n"
        )

        if notes:
            entry += f"<i>Notes:</i> {notes}\n"

        entry += f"<i>Price:</i> {price}"

        return entry

    def _save_batch(self):
        """Sends a batch of courses to Telegram and saves their codes."""
        if not self.batch:
            return

        # Build the batch message
        batch_message = f"<b>New courses found: {len(self.batch)}</b>\n\n"

        for idx, course_data in enumerate(self.batch, 1):
            course_entry = self._format_course_entry(course_data)
            if course_entry:
                batch_message += course_entry
                if idx < len(self.batch):
                    batch_message += "\n━━━━━━━━━━━━━\n\n"

        # Send the message (raises an exception on failure)
        self.telegram.send(batch_message)

        # Save the codes in storage
        for course_data in self.batch:
            code = self._extract_course_code(course_data)
            if code:
                self.storage.add(code)

        # Save codes to disk immediately to avoid duplicates
        self.storage.save()

        logger.info("Sent a notification about %s new courses", len(self.batch))
        self.batch = []

    def add(self, result):
        """Adds a course result, checks whether it exists, and saves the batch when it reaches the chunk size."""
        self.total_found += 1

        code = self._extract_course_code(result)
        if not code:
            return

        # Check whether the code exists
        if self.storage.exists(code):
            return

        # Add to the batch of new courses
        self.batch.append(result)
        self.total_new += 1

        # Save the batch if it has reached the chunk size
        # Rate limiting is handled by Telegram.send()
        if len(self.batch) >= self.chunk:
            self._save_batch()


def main():
    """Main function."""
    telegram = Telegram()
    try:
        logger.info("Starting VHS course monitoring")

        storage = CodeStorage()
        found = 0
        with Service(telegram=telegram, storage=storage, chunk=5) as service:
            searcher = Searcher()

            for result in searcher.search():
                service.add(result)
                found += 1

        logger.info("Processing completed")
        logger.info("Total courses found: %s", service.total_found)
        logger.info("New courses: %s", service.total_new)
    except Exception as e:
        try:
            telegram.send("Error!")
        except Exception as e:
            logger.error("Error sending a Telegram message: %s", e)
        raise e
    if not found:
        telegram.send("No courses found at all!")


if __name__ == "__main__":
    main()

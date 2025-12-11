import os
import time
from threading import Lock
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import requests

# Загружаем переменные окружения из .env файла
load_dotenv()


class CodeStorage:
    """Класс для работы с хранением кодов курсов."""

    def __init__(self, filename="course_codes.txt"):
        self.filename = filename
        self._codes = self._load()

    def _load(self):
        """Загружает сохраненные коды курсов из файла."""
        if not os.path.exists(self.filename):
            return set()
        try:
            with open(self.filename, "r", encoding="utf-8") as f:
                return set(line.strip() for line in f if line.strip())
        except Exception as e:
            print(f"⚠️  Ошибка при загрузке кодов курсов: {e}")
            return set()

    def exists(self, code):
        """Проверяет, существует ли код."""
        return code in self._codes

    def add(self, code):
        """Добавляет код в память."""
        self._codes.add(code)

    def save(self):
        """Сохраняет все коды в файл."""
        try:
            with open(self.filename, "w", encoding="utf-8") as f:
                for code in sorted(self._codes):
                    f.write(f"{code}\n")
            print(f"✅ Сохранено {len(self._codes)} кодов курсов в {self.filename}")
        except Exception as e:
            print(f"❌ Ошибка при сохранении кодов курсов: {e}")


class RateLimiter:
    """Семафор для ограничения частоты отправки сообщений."""

    def __init__(self, is_group=False):
        """
        Инициализирует rate limiter.

        Args:
            is_group: True для групп (20 сообщений в минуту), False для личных чатов (1 сообщение в секунду)
        """
        self._lock = Lock()
        self._is_group = is_group

        if self._is_group:
            # Группа: не более 20 сообщений в минуту
            self._max_messages_per_minute = 20
            self._window_seconds = 60.0
            self._message_timestamps = (
                []
            )  # Очередь временных меток отправленных сообщений
        else:
            # Личный чат: не более 1 сообщения в секунду
            self._min_interval = 1.0
            self._last_send_time = 0

    def acquire(self):
        """
        Блокирует выполнение, если необходимо, чтобы соблюсти rate limits.
        Должен вызываться перед отправкой сообщения.
        """
        with self._lock:
            current_time = time.time()

            if self._is_group:
                # Для групп: проверяем скользящее окно в 1 минуту
                # Удаляем временные метки старше 1 минуты
                cutoff_time = current_time - self._window_seconds
                self._message_timestamps = [
                    ts for ts in self._message_timestamps if ts > cutoff_time
                ]

                # Если в последнюю минуту уже отправлено 20 сообщений, ждем
                if len(self._message_timestamps) >= self._max_messages_per_minute:
                    # Ждем, пока не пройдет минута с момента самого старого сообщения
                    oldest_timestamp = self._message_timestamps[0]
                    wait_time = self._window_seconds - (current_time - oldest_timestamp)
                    if wait_time > 0:
                        time.sleep(wait_time)
                        # Обновляем время после ожидания
                        current_time = time.time()
                        # Еще раз очищаем старые метки
                        cutoff_time = current_time - self._window_seconds
                        self._message_timestamps = [
                            ts for ts in self._message_timestamps if ts > cutoff_time
                        ]
            else:
                # Для личных чатов: простая проверка интервала
                time_since_last_send = current_time - self._last_send_time
                if time_since_last_send < self._min_interval:
                    sleep_time = self._min_interval - time_since_last_send
                    time.sleep(sleep_time)
                    current_time = time.time()

    def record(self):
        """
        Записывает факт отправки сообщения.
        Должен вызываться после успешной отправки сообщения.
        """
        with self._lock:
            send_time = time.time()
            if self._is_group:
                self._message_timestamps.append(send_time)
            else:
                self._last_send_time = send_time


class Telegram:
    """Класс для отправки сообщений в Telegram с умным rate limiting."""

    def __init__(self, bot_token=None, chat_id=None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

        # Определяем тип чата: отрицательный chat_id = группа
        try:
            chat_id_int = int(self.chat_id) if self.chat_id else 0
            is_group = chat_id_int < 0
        except (ValueError, TypeError):
            is_group = False

        # Создаем rate limiter в зависимости от типа чата
        self._rate_limiter = RateLimiter(is_group=is_group)

    def send(self, message):
        """Отправляет сообщение в Telegram с соблюдением rate limits."""
        if not self.bot_token or not self.chat_id:
            raise ValueError(
                "Telegram не настроен: отсутствует TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID"
            )

        # Используем семафор для ограничения частоты отправки
        self._rate_limiter.acquire()

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"}

        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 400:
            print(f"❌ Не удалось отправить сообщение в Telegram: {response.text}")
        response.raise_for_status()

        # Записываем факт отправки сообщения
        self._rate_limiter.record()

        return response.json()


class Searcher:
    """Класс для поиска курсов на сайте VHS."""

    def __init__(self):
        self.url_search = (
            "https://www.vhsit.berlin.de/VHSKURSE/BusinessPages/CourseSearch.aspx"
        )
        self.url_courselist = (
            "https://www.vhsit.berlin.de/VHSKURSE/BusinessPages/CourseList.aspx"
        )
        self.session = requests.Session()

    def _get_value(self, soup, name):
        """Извлекает значение из input по имени."""
        tag = soup.find("input", {"name": name})
        return tag["value"] if tag and "value" in tag.attrs else ""

    def _extract_course_code(self, course_data):
        """Извлекает код курса из данных курса (индекс 3)."""
        if len(course_data) > 3:
            code = course_data[3].strip()
            return code if code else None
        return None

    def search(self):
        """Генератор, который возвращает найденные курсы."""
        # 1. Загружаем страницу поиска
        response = self.session.get(self.url_search)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        viewstate = self._get_value(soup, "__VIEWSTATE")
        eventvalidation = self._get_value(soup, "__EVENTVALIDATION")
        viewstategen = self._get_value(soup, "__VIEWSTATEGENERATOR")

        # 2. Активируем вкладку "Erweitert" (Расширенный поиск)
        print("Активируем вкладку 'Erweitert'...")
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
        print("Вкладка 'Erweitert' активирована")

        # 3. Формируем параметры поиска
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

        # 4. Отправляем POST-запрос с параметрами расширенного поиска
        print("Отправляем запрос расширенного поиска...")
        result = self.session.post(self.url_search, data=payload)
        result.raise_for_status()
        soup_result = BeautifulSoup(result.text, "html.parser")
        with open("output.html", "w") as f:
            f.write(result.text)

        # 5. Обрабатываем все страницы результатов
        current_soup = soup_result
        page_num = 1

        for page_num in range(1, 30):
            # Извлекаем курсы с текущей страницы
            page_courses = []
            for course in current_soup.select(
                "#ctl00_Content_ILDataGrid1 tr.DataGridItem, #ctl00_Content_ILDataGrid1 tr.DataGridAlternatingItem"
            ):
                tds = course.find_all("td")
                cols = [td.get_text(strip=True) for td in tds]

                # Найти ссылку в колонке заголовка курса
                link = ""
                for td in tds:
                    if "DataGridItemCourseTitle" in td.get("class", []):
                        a_tag = td.find("a", href=True)
                        if a_tag:
                            link = a_tag["href"]
                        break
                # Добавим ссылку как дополнительную колонку
                cols.append(link)

                if cols:
                    code = self._extract_course_code(cols)
                    if code:
                        page_courses.append(cols)
                        yield cols
            print(f"Страница {page_num}: найдено {len(page_courses)} курсов")

            # Проверяем, есть ли кнопка "следующая страница" и не отключена ли она
            next_button = current_soup.find(
                "input", {"name": lambda x: x and "ctl04" in x and "ILDataGrid1" in x}
            )

            if not next_button:
                print("Кнопка следующей страницы не найдена")
                break

            button_class = next_button.get("class", [])
            if isinstance(button_class, str):
                button_class = button_class.split()

            if "aspNetDisabled" in button_class:
                print("Достигнута последняя страница")
                break

            button_name = next_button.get("name")
            if not button_name:
                print("Не удалось найти имя кнопки")
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
    """Основной сервис для обработки курсов."""

    def __init__(self, telegram: Telegram, storage: CodeStorage, chunk=5):
        self.chunk = chunk
        self.storage = storage
        self.telegram = telegram
        self.batch = []  # Батч новых курсов для отправки
        self.total_found = 0
        self.total_new = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Сохраняет несохраненные элементы при выходе."""
        self._save_batch()  # _save_batch() уже сохраняет коды на диск

    def _extract_course_code(self, course_data):
        """Извлекает код курса из данных курса (индекс 3)."""
        if len(course_data) > 3:
            code = course_data[3].strip()
            return code if code else None
        return None

    def _format_course_entry(self, course_data):
        """Форматирует информацию об одном курсе для добавления в групповое сообщение."""
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

        # Очищаем дату/время от \r\n
        date_time = date_time.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")

        entry = (
            f"━━━━━━━━━━━━━━━\n"
            f"<b>{name}</b>\n"
            f"<i>Код:</i> {code}\n"
            f"<i>Район:</i> {district}\n"
            f"<i>Дата/Времяi</b> {date_time}\n"
            f"<i>Места:</i> {places}\n"
            f"<i>Ссылка:</i> https://www.vhsit.berlin.de/VHSKURSE/BusinessPages/{link}\n"
        )

        if notes:
            entry += f"<b>Примечания:</b> {notes}\n"

        entry += f"<b>Цена:</b> {price}"

        return entry

    def _save_batch(self):
        """Отправляет батч курсов в Telegram и сохраняет коды."""
        if not self.batch:
            return

        # Формируем сообщение для батча
        batch_message = f"<b>🆕 Найдено новых курсов: {len(self.batch)}</b>\n\n"

        for idx, course_data in enumerate(self.batch, 1):
            course_entry = self._format_course_entry(course_data)
            if course_entry:
                batch_message += course_entry
                if idx < len(self.batch):
                    batch_message += "\n\n"

        # Отправляем сообщение (будет падать при ошибке)
        self.telegram.send(batch_message)

        # Сохраняем коды в хранилище
        for course_data in self.batch:
            code = self._extract_course_code(course_data)
            if code:
                self.storage.add(code)

        # Сохраняем коды на диск сразу, чтобы избежать дубликатов
        self.storage.save()

        print(f"✅ Отправлено уведомление о {len(self.batch)} новых курсах")
        self.batch = []

    def add(self, result):
        """Добавляет результат курса. Проверяет существование, добавляет в батч, сохраняет при достижении chunk."""
        self.total_found += 1

        code = self._extract_course_code(result)
        if not code:
            return

        # Проверяем, существует ли код
        if self.storage.exists(code):
            return

        # Добавляем в батч новых курсов
        self.batch.append(result)
        self.total_new += 1

        # Если батч достиг размера chunk, сохраняем его
        # Rate limiting обрабатывается в Telegram.send()
        if len(self.batch) >= self.chunk:
            self._save_batch()


def main():
    """Основная функция."""
    telegram = Telegram()
    try:
        print("🚀 Запуск мониторинга курсов VHS\n")

        storage = CodeStorage()
        found = 0
        with Service(telegram=telegram, storage=storage, chunk=5) as service:
            searcher = Searcher()

            for result in searcher.search():
                service.add(result)
                found += 1

        print("\n✅ Обработка завершена")
        print(f"📊 Всего найдено курсов: {service.total_found}")
        print(f"🆕 Новых курсов: {service.total_new}")
    except Exception as e:
        try:
            telegram.send(f"❌ Ошибка: {e}")
        except Exception as e:
            print(f"❌ Ошибка при отправке сообщения в Telegram: {e}")
        raise e
    if not found:
        telegram.send("❌ Не найдено курсов вообще!")


if __name__ == "__main__":
    main()

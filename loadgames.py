import requests
import datetime

def getlastlichessgames(username,max_games,period):

    time_now = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    time_prev = time_now - int(datetime.timedelta(days=period).total_seconds() * 1000)
    url = f'https://lichess.org/api/games/user/{username}?tags=true&clocks=false&evals=false&opening=false&literate=false&max={max_games}&since={time_prev}&until={time_now}&perfType=blitz%2Crapid%2Cclassical%2Ccorrespondence%2Cstandard'

    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        file_path = f'D:\\ChessHelper\\chessdata\\lichess\\{username}.pgn'
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при скачивании файла: {e}")

    with open(f'D:\\ChessHelper\\chessdata\\lichess\\{username}.pgn', encoding='UTF-8') as file:
        text = file.read()

    if text.strip() == '':
        return list()

    gameslist = list()
    numofgames = text.count('[Event')
    for v in range(numofgames - 1):
        gameslist.append(text[:text.find('[Event',1)].strip())
        text = text[text.find('[Event',1):]
    gameslist.append(text.strip())
    return gameslist

def getlastchesscomgames(username, max_games, period):
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(days=period)
    start_ts = start.timestamp()
    end_ts = now.timestamp()

    months = []
    y, m = start.year, start.month
    while (y, m) <= (now.year, now.month):
        months.append((y, f"{m:02d}"))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1

    headers = {"User-Agent": "MyChessBot/1.0 (+https://t.me/@Justachessbot)"}
    all_games = []

    for year, mon in months:
        url = f"https://api.chess.com/pub/player/{username}/games/{year}/{mon}"
        try:
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as e:
            print(f"[Chess.com] Ошибка при запросе {url}: {e}")
            continue

        for game in payload.get("games", []):
            end_time = game.get("end_time")
            if not isinstance(end_time, int):
                continue

            if start_ts <= end_time <= end_ts:
                all_games.append(game)

    if not all_games:
        return list()

    sorted_games = sorted(all_games, key=lambda g: g["end_time"], reverse=True)
    selected = sorted_games[:max_games]

    return [g.get("pgn", "") for g in selected]
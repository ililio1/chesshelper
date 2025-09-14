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

    gameslist = list()
    numofgames = text.count('[Event')
    for i in range(numofgames - 1):
        gameslist.append(text[:text.find('[Event',1)].strip())
        text = text[text.find('[Event',1):]
    gameslist.append(text.strip())
    return gameslist

def getlastchesscomgames(username,max_games,period):
    time_now = datetime.datetime.now(datetime.UTC)
    time_prev = time_now - datetime.timedelta(days=period)
    fmt_mon_now  = time_now.strftime('%m')
    fmt_mon_prev = time_prev.strftime('%m')
    headers = {'User-Agent': 'MyChessBot/1.0 (+https://t.me/@Justachessbot)'}

    url_now = f"https://api.chess.com/pub/player/{username}/games/{time_now.year}/{fmt_mon_now}"
    url_prev = f"https://api.chess.com/pub/player/{username}/games/{time_prev.year}/{fmt_mon_prev}"
    urls = [url_now]
    if time_now.month != time_prev.month:
        urls.append(url_prev)
    data = {'games': []}

    for url in urls:
        try:
            resp = requests.get(url, headers=headers).json()
        except requests.RequestException as e:
            print(f"Ошибка при запросе архива: {e}")
            continue
        data['games'] += resp.get('games', [])

    gamelist = list()

    for game in data.get('games', [])[:max_games]:
        pgn = game.get('pgn', '')
        gamelist.append(pgn)

    return gamelist

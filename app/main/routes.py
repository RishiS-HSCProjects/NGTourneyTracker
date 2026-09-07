from dataclasses import dataclass, field
import json
from flask import Blueprint, render_template, redirect, url_for, request, current_app, jsonify, abort
from flask_login import current_user, login_required
from app.models.tournament import (
    Tournament,
    TournamentArchiveException,
    TournamentPrizes,
    RewardPackageTypes,
    format_punishments_for_ui,
)
from app.utils.utils import flash, flash_all_form_errors, restore_form_state, save_form_state
from app.utils.discord_webhook_utils import ChannelWebhookUrl, format_placement_lines, format_prize_lines, send as discord_send
from time import time
from datetime import datetime, UTC
from werkzeug.exceptions import HTTPException
from sqlalchemy.exc import OperationalError

main_bp = Blueprint("main", __name__, template_folder="templates", static_folder="static", static_url_path="/main/static")

@main_bp.route('/')
def dashboard():
    @dataclass
    class KPI:
        title: str
        value: str
        detail: str = ""
        hover_text: str = ""
        href: str | None = None
        attrs: dict[str, str] = field(default_factory=dict)

    def _format_round_duration(seconds: float) -> str:
        """ Format round duration to h/m/s """
        total_seconds = max(0, int(round(seconds)))
        if total_seconds < 60:
            return f"{total_seconds} seconds"
        if total_seconds < 3600:
            minutes = total_seconds // 60
            remainder = total_seconds % 60
            return f"{minutes}m {remainder}s"
        hours = total_seconds // 3600
        remainder = total_seconds % 3600
        minutes = remainder // 60

        if not minutes: # Falsy minute value '0'
            return f"{hours}h"

        return f"{hours}h {minutes}m"

    def _format_countdown(seconds: float) -> str:
        """ Format countdown in to h/m/s """
        total_seconds = max(0, int(seconds))
        if total_seconds < 60:
            return f"{total_seconds}s"
        if total_seconds < 3600:
            minutes = total_seconds // 60
            seconds_left = total_seconds % 60
            return f"{minutes}m {seconds_left}s"
        if total_seconds < 86400:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            return f"{hours}h" if not minutes else f"{hours}h {minutes}m"

        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        return f"{days}d" if not hours else f"{days}d {hours}h"

    now_ts = int(datetime.now(UTC).timestamp())
    tournaments = Tournament.query.order_by(Tournament.start_unix.asc()).all()

    # Get first tournament where the condition is met. Iterate through until condition is met.
    active_tournament = next((t for t in tournaments if t.start_unix <= now_ts <= t.end_unix), None)
    next_tournament = next((t for t in tournaments if t.start_unix > now_ts), None)
    last_tournament = next((t for t in reversed(tournaments) if t.end_unix < now_ts), None) # Reverse for faster computation

    kpis: list[KPI] = []

    if active_tournament:
        current_round = min(
            active_tournament.round_count,
            max(1, int((now_ts - active_tournament.start_unix) // active_tournament.round_duration) + 1),
        )
        next_round_unix = min(
            active_tournament.end_unix,
            active_tournament.start_unix + (current_round * active_tournament.round_duration),
        )
        kpis.append(KPI(
            title="Active Tournament",
            value=str(current_round),
            detail=f"of {active_tournament.round_count} rounds",
            hover_text=f"{active_tournament.name} is currently running.",
            href=url_for('main.tournament_editor', tournament_id=active_tournament.id),
        ))
        kpis.append(KPI(
            title="Status",
            value="LIVE",
            detail=active_tournament.name,
            hover_text=f"Started {datetime.fromtimestamp(active_tournament.start_unix, UTC).strftime('%a, %d %b %Y %H:%M UTC')}",
            href=url_for('main.tournament_editor', tournament_id=active_tournament.id)
        ))
        kpis.append(KPI(
            title="Round Duration",
            value=_format_round_duration(active_tournament.round_duration),
            detail=f"{_format_countdown(next_round_unix - now_ts)} remaining",
            hover_text=f"Each round lasts about {active_tournament.round_duration:.0f} seconds.",
            attrs={
                "data-kpi-kind": "countdown",
                "data-kpi-target-unix": str(next_round_unix),
                "data-kpi-countdown-field": "detail",
            },
        ))
    elif next_tournament:
        kpis.append(KPI(
            title="Time to Next Tourney",
            value="",
            detail="until start",
            hover_text=f"Next tournament: {next_tournament.name} starts at {datetime.fromtimestamp(next_tournament.start_unix, UTC).astimezone().strftime('%a, %d %b %Y %H:%M %Z')}",
            href=url_for('main.tournament_editor', tournament_id=next_tournament.id),
            attrs={
                "data-kpi-kind": "countdown",
                "data-kpi-target-unix": str(next_tournament.start_unix),
            },
        ))
        kpis.append(KPI(
            title="Status",
            value="UP NEXT",
            detail=next_tournament.name,
            hover_text="No tournament is currently running.",
            href=url_for('main.tournament_editor', tournament_id=next_tournament.id)
        ))
        kpis.append(KPI(
            title="Round Duration",
            value=_format_round_duration(next_tournament.round_duration),
            detail=f"{str(max(1, int(round(next_tournament.round_duration))))} seconds per round",
            hover_text=f"Future tournament round length: {next_tournament.round_duration:.0f} seconds."
        ))
    else:
        if last_tournament:
            days_ago = max(0, (now_ts - last_tournament.end_unix) // 86400)
            kpis.append(KPI(
                title="Last Tournament",
                value=str(days_ago),
                detail=f"day{'s' * (days_ago != 1)} ago", # Handle plural case
                href=url_for('main.tournament_editor', tournament_id=last_tournament.id),
                hover_text=f"Last tournament: {last_tournament.name}"
            ))
        else:
            kpis.append(KPI(
                title="Last Tournament",
                value="0",
                detail="No tournaments yet",
                hover_text="Create the first tournament to populate the dashboard."
            ))

        kpis.append(KPI(
            title="Status",
            value="IDLE",
            detail="No active tournament",
            hover_text="No tournament is currently active."
        ))

        kpis.append(KPI(
            title="Round Duration",
            value="0",
            detail="N/A until a tournament exists",
            hover_text="Round duration is only available once a tournament exists."
        ))

    if last_tournament:
        overall_leaderboard = last_tournament.get_leaderboard(round_num=None)
        last_winner = overall_leaderboard.get_entries(limit=1)
        if last_winner:
            winner = last_winner[0]
            kpis.append(KPI(
                title="Last Overall Winner",
                value=f"{winner.player}",
                detail=f"{str(winner.score)} kills",
                hover_text=f"Top performer from {last_tournament.name}.",
                href=url_for('main.tournament_editor', tournament_id=last_tournament.id)
            ))
        else:
            kpis.append(KPI(
                title="Last Overall Winner",
                value="N/A",
                detail=f"{last_tournament.name} no leaderboard data",
                hover_text="The last tournament has no archived overall leaderboard yet.",
                href=url_for('main.tournament_editor', tournament_id=last_tournament.id)
            ))
    else:
        kpis.append(KPI(
            title="Last Overall Winner",
            value="N/A",
            detail="No finished tournaments yet",
            hover_text="There is no completed tournament to summarize yet."
        ))


    # Get data for last tournament podium, if available
    podium_players = []
    if (last_validated_tourney := next((t for t in reversed(tournaments) if t.recipients_validated_status), None)):
        last_lb = last_validated_tourney.get_leaderboard(round_num=None).get_entries(limit=3)
        try:
            # On validation, avatar is pulled from top victors. Dig into the validation registry for that value (instead of pulling it each time)
            for player in last_validated_tourney.validation_registry().get('recipients', {}).get('global', {}).values():
                podium_players.append({
                    'xuid': player.get('xuid'),
                    'ign': player['player'],
                    'avatar': player.get('avatar') or url_for('main.static', filename='img/head.png'),
                    'value': next((entry.score for entry in last_lb if entry.player == player['player']), None)
                })
        except Exception as e:
            current_app.logger.warning(f"Failed to fetch validation registry for last tournament podium: {e}")
            # Fallback to just showing ign and score from the leaderboard if validation registry fails
            podium_players = None # Hide podium if validation registry fails

    return render_template('dashboard.html', kpis=kpis, last_validated_tourney=last_validated_tourney, podium_players=podium_players)

@main_bp.route('/scheduler', methods=['GET', 'POST'])
def scheduler(open_add_modal=False):
    from app.forms import AddTournamentForm

    kwargs = {}
    now = int(datetime.now(UTC).timestamp())

    # 1. Fetch Tournament Data
    previous = Tournament.query.filter(Tournament.end_unix < now).order_by(Tournament.end_unix.desc()).all() or []

    kwargs.update({
        'previous_tournaments': list(reversed(previous)),
        'current_tournament': Tournament.query.filter(Tournament.start_unix <= now, Tournament.end_unix >= now).order_by(Tournament.start_unix.asc()).first(),
        'future_tournaments': Tournament.query.filter(Tournament.start_unix > now).order_by(Tournament.start_unix.asc()).all()
    })

    # 2. Handle Form Logic
    add_form = None
    if current_user.is_authenticated:
        add_form = restore_form_state(AddTournamentForm())

        if add_form.validate_on_submit():
            # Check for time overlap
            start, end = add_form.start_unix.data, add_form.end_unix.data
            rounds = add_form.round_count.data

            if start is None or end is None or rounds is None:
                flash('Please provide valid tournament dates and round count.', 'error')
            else:
                start = int(start)
                end = int(end)
                rounds = int(rounds)
                overlap = Tournament.query.filter(Tournament.start_unix < end, Tournament.end_unix > start).first()

                if overlap:
                    flash(f'Time overlap with tournament "{overlap.name}"', 'error')
                else:
                    Tournament.create(
                        name=(add_form.name.data or '').strip(),
                        start_unix=start,
                        end_unix=end,
                        stat_columns=add_form.stat_columns.data or [],
                        round_count=rounds,
                        created_by=current_user.id,
                        tournament_info_discord_message=(add_form.discord_message.data or '').strip(),
                        prizes=TournamentPrizes(
                            overall_first=(add_form.global_first_prize.data or '').strip(),
                            overall_second=(add_form.global_second_prize.data or '').strip(),
                            overall_third=(add_form.global_third_prize.data or '').strip(),
                            round_first=(add_form.round_first_prize.data or '').strip(),
                            round_second=(add_form.round_second_prize.data or '').strip(),
                            round_third=(add_form.round_third_prize.data or '').strip(),
                        )
                    )
                    flash('Tournament added successfully!', 'success')
                    return redirect(url_for('main.scheduler'))

        elif request.method == 'POST':
            flash_all_form_errors(add_form)
        else:
            add_form.round_count.data = 7 # Default round count to 7 for better UX, since most tournaments have 7 rounds
            add_form.round_first_prize.data = "1 month Titan Rank, 3000 XP"
            add_form.round_second_prize.data = "2500 XP"
            add_form.round_third_prize.data = "2000 XP"
            add_form.global_first_prize.data = "1 month Titan Rank, +10 Levels"
            add_form.global_second_prize.data = "+7 Levels"
            add_form.global_third_prize.data = "+5 Levels"

    # 3. Consolidate Remaining UI State
    kwargs['add_form'] = add_form
    kwargs['show_add_modal'] = open_add_modal or (add_form.errors if add_form else False)

    return render_template('scheduler.html', **kwargs)

@main_bp.route('/scheduler/<int:tournament_id>', methods=['GET', 'POST'])
def tournament_editor(tournament_id: int):
    from app.forms import AddTournamentForm
    from app import db

    tourney = Tournament.query.get_or_404(tournament_id)
    kwargs = {'tournament': tourney}
    if not tourney.tournament_info_discord_status and tourney.is_expired:
        tourney.tournament_info_discord_status = True
        db.session.commit()

    form = AddTournamentForm(request.form if request.method == 'POST' else None)
    if request.method == 'GET':
        form.name.data = tourney.name
        form.start_unix.data = tourney.start_unix
        form.end_unix.data = tourney.end_unix
        form.stat_columns.data = tourney.stat_columns or []
        form.round_count.data = tourney.round_count
        form.discord_message.data = getattr(tourney, 'tournament_info_discord_message', None) or ''
        prizes = getattr(tourney, 'prizes', {}) or {}
        form.global_first_prize.data = prizes.get('overall_first', '')
        form.global_second_prize.data = prizes.get('overall_second', '')
        form.global_third_prize.data = prizes.get('overall_third', '')
        form.round_first_prize.data = prizes.get('round_first', '')
        form.round_second_prize.data = prizes.get('round_second', '')
        form.round_third_prize.data = prizes.get('round_third', '')
    elif request.method == 'POST':
        save_form_state(form, form_id='tournament_editor')

    # Only process changes if authenticated.
    # This way, even if the js is intercepted, the db will not change unless the server says they are authenticated.
    if current_user.is_authenticated:
        form = restore_form_state(form)

        if form.validate_on_submit():
            start, end, rounds = form.start_unix.data, form.end_unix.data, form.round_count.data

            if start is None or end is None or rounds is None:
                flash('Please provide valid tournament dates and round count.', 'error')
                return redirect(url_for('main.tournament_editor', tournament_id=tourney.id))

            start = int(start)
            end = int(end)
            rounds = int(rounds)

            overlap = Tournament.query.filter(
                Tournament.id != tourney.id,
                Tournament.start_unix < end,
                Tournament.end_unix > start
            ).first()

            if overlap:
                flash(f'Time overlap with tournament "{overlap.name}"', 'error')
            elif rounds < 1:
                flash('Round count must be at least 1', 'error')
            else:
                tourney.name = (form.name.data or '').strip()
                tourney.start_unix, tourney.end_unix, tourney.round_count = int(start), int(end), int(rounds)
                tourney.stat_columns = form.stat_columns.data or []
                tourney.prizes = {
                    'overall_first': (form.global_first_prize.data or '').strip(),
                    'overall_second': (form.global_second_prize.data or '').strip(),
                    'overall_third': (form.global_third_prize.data or '').strip(),
                    'round_first': (form.round_first_prize.data or '').strip(),
                    'round_second': (form.round_second_prize.data or '').strip(),
                    'round_third': (form.round_third_prize.data or '').strip(),
                }

                if not tourney.tournament_info_discord_status:
                    tourney.tournament_info_discord_message = (form.discord_message.data or '').strip()

                db.session.commit()
                flash('Tournament updated', 'success')
                return redirect(url_for('main.tournament_editor', tournament_id=tourney.id))

        elif request.method == 'POST':
            flash_all_form_errors(form)

    current_round = None
    if tourney.is_active and tourney.round_duration > 0:
        elapsed = int(time()) - tourney.start_unix
        current_round = min(tourney.round_count, max(1, int(elapsed // tourney.round_duration) + 1))

    if tourney.is_active and current_round:
        round_numbers = list(range(1, current_round + 1))
    elif tourney.is_expired:
        round_numbers = list(range(1, tourney.round_count + 1))
    else:
        round_numbers = []

    now_ts = int(datetime.now(UTC).timestamp())
    start_dt = datetime.fromtimestamp(tourney.start_unix, UTC)
    end_dt = datetime.fromtimestamp(tourney.end_unix, UTC)

    # UI formatting for punishments is centralized in `format_punishments_for_ui`.

    all_disqualified_players = {}
    validation_data = tourney.archives.get('validation') if isinstance(tourney.archives, dict) else None
    if isinstance(validation_data, dict):
        for disq_player_data in validation_data.get('disqualifiedPlayers') or []:
            player_name = disq_player_data.get('player', 'Unknown')
            punishments = disq_player_data.get('punishments', [])
            formatted_punishments = format_punishments_for_ui(punishments, tourney.start_unix)

            if player_name not in all_disqualified_players:
                all_disqualified_players[player_name] = []

            all_disqualified_players[player_name].append({
                'round': disq_player_data.get('round', 'Validation'),
                'punishments': formatted_punishments,
            })

    # Build round leaderboards
    round_leaderboards = []

    for r in round_numbers:
        round_data = {
            'round_num': r,
            'leaderboard': tourney.get_leaderboard(round_num=r),
        }

        round_leaderboards.append(round_data)

    def _banner_time(dt, ts):
        """ Create string for banner time on the page. Uses local timezone. """
        local_dt = dt.astimezone()
        local_text = f"{local_dt.day} {local_dt.strftime('%B %Y at %H:%M')}"
        return f"{local_text} ({_relative_logic(ts, now_ts)})"

    kwargs.update({
        'form': form,
        'leaderboard_overall': tourney.get_leaderboard(round_num=None),
        'current_round': current_round,
        'round_leaderboards': round_leaderboards,
        'all_disqualified_players': all_disqualified_players,
        'selected_round': current_round or (round_numbers[-1] if round_numbers else None),
        'cache_stats_locked': tourney.is_archived(),
        'reward_package_options': list(RewardPackageTypes),
        'tourney_details': {
            'start_gmt': start_dt.strftime('%a, %d %b %Y %H:%M:%S GMT'),
            'end_gmt': end_dt.strftime('%a, %d %b %Y %H:%M:%S GMT'),
            'start_local_title': f"{start_dt.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
            'end_local_title': f"{end_dt.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
            'start_relative_title': f"{_relative_logic(tourney.start_unix, now_ts)}",
            'end_relative_title': f"{_relative_logic(tourney.end_unix, now_ts)}",
            'start_banner_text': _banner_time(start_dt, tourney.start_unix),
            'end_banner_text': _banner_time(end_dt, tourney.end_unix),
            'round_secs': f"{tourney.round_duration:.0f}",
            "stat_columns": ",".join(tourney.stat_columns)
        }
    })

    return render_template('tournament_detail.html', **kwargs)

@main_bp.route('/scheduler/<int:tournament_id>/package/stats', methods=['GET', 'POST'])
def tournament_package_stats(tournament_id: int):
    """ Package stats in a SQL tuple form. """
    if not (current_user and current_user.is_manager()):
        return jsonify({'success': False, 'message': 'Access denied. Managers only.'}), 403

    if not (tourney := Tournament.query.get_or_404(tournament_id)):
        return jsonify({'success': False, 'message': f'Tournament {tournament_id} not found.'}), 404

    current_app.logger.info(request.method)
    packages = tourney.get_reward_packages()
    return jsonify({'success': True, 'packages': packages})

@main_bp.route('/scheduler/<int:tournament_id>/validate_recipients', methods=['POST'])
def tournament_validate_recipients(tournament_id: int):
    if not (current_user and current_user.is_manager()):
        return jsonify({'success': False, 'message': 'Access denied. Managers only.'}), 403

    tourney = Tournament.query.get_or_404(tournament_id)

    if tourney.recipients_validated_status is not False:
        return jsonify({'success': False, 'message': 'Validation already in progress or completed. Please check back later.'}), 400
    else: tourney.recipients_validated = int(current_user.id) # Prevents duplicate validations

    from app.models.user import User
    from app import db

    def _normalize_punishment(punishment: dict):
        punishment_type = str(punishment.get('type') or '').upper()
        if punishment_type not in ['BAN', 'MUTE']:
            return None

        issued_at = punishment.get('issuedAt')
        if not isinstance(issued_at, int):
            return None

        valid_until = punishment.get('validUntil')
        if valid_until is not None and not isinstance(valid_until, int):
            valid_until = None

        player = punishment.get('player')
        if not isinstance(player, str) or not player:
            return None

        return {
            'id': punishment.get('id'),
            'type': punishment_type,
            'issued_at': issued_at,
            'end_at': valid_until,
            'player': player,
        }

    def _is_disqualifying(punishment: dict) -> bool:
        """ Decides whether or not a player should be disqualified. """
        end_at = punishment.get('end_at')
        if end_at is None:
            # Players with permanent punishments should be disqualified.
            return True

        lookback_seconds = 30 * 24 * 60 * 60 if punishment.get('type') == 'BAN' else 7 * 24 * 60 * 60
        cutoff = tourney.start_unix - lookback_seconds
        return end_at >= tourney.start_unix or end_at >= cutoff

    def _player_data(player_name: str) -> dict | None:
        if player_name in seen_players:
            return player_data_cache.get(player_name)

        seen_players.add(player_name)
        try:
            data = User.get_player_data(player_name)
        except Exception:
            player_data_cache[player_name] = None
            return None

        if not isinstance(data, dict):
            player_data_cache[player_name] = None
            return None

        player_data_cache[player_name] = data
        return data

    def _player_disqualification_record(player_name: str) -> dict | None:
        """ Get punishment record if disqualified. """
        if player_name in seen_players:
            return disqualification_cache.get(player_name)

        data = _player_data(player_name)

        raw_punishments = []
        if isinstance(data, dict):
            raw_punishments = data.get('punishmentsNew') or data.get('punishments') or []

        latest_by_type: dict[str, dict] = {}
        for raw in raw_punishments:
            if not isinstance(raw, dict):
                continue
            normalized = _normalize_punishment(raw)
            if not normalized:
                continue

            current = latest_by_type.get(normalized['type'])
            if current is None or normalized['issued_at'] >= current['issued_at']:
                latest_by_type[normalized['type']] = normalized

        disq_punishments = [p for p in latest_by_type.values() if _is_disqualifying(p)]
        if not disq_punishments:
            disqualification_cache[player_name] = None
            return None

        record = {
            'player': player_name,
            'punishments': [
                {
                    'id': p.get('id'),
                    'issued_at': p.get('issued_at'),
                    'end_at': p.get('end_at'),
                    'type': p.get('type'),
                }
                for p in disq_punishments
            ],
        }

        disqualification_cache[player_name] = record
        return record

    seen_players: set[str] = set()
    player_data_cache: dict[str, dict | None] = {}
    disqualification_cache: dict[str, dict | None] = {}

    def _select_top_qualified(entries, required: int = 3):
        qualified = []
        disqualified_players = []

        for entry in entries:
            if len(qualified) >= required:
                break

            player = entry.player if hasattr(entry, 'player') else None
            if not isinstance(player, str) or not player:
                continue

            record = _player_disqualification_record(player)
            if record:
                disqualified_players.append(record)
                continue

            qualified.append(entry)

        return qualified, disqualified_players

    disqualified_records: dict[str, dict] = {}

    round_firsts = []
    round_seconds = []
    round_thirds = []

    for r in range(1, tourney.round_count + 1):
        round_leaderboard = tourney.get_leaderboard(round_num=r)
        entries = round_leaderboard.entries if hasattr(round_leaderboard, 'entries') else round_leaderboard.get_top(100)
        eligible, round_disqualified = _select_top_qualified(entries, required=3)
        for record in round_disqualified:
            disqualified_records.setdefault(record['player'], record)

        round_firsts.append({'round': r, 'player': eligible[0].player if len(eligible) > 0 else None})
        round_seconds.append({'round': r, 'player': eligible[1].player if len(eligible) > 1 else None})
        round_thirds.append({'round': r, 'player': eligible[2].player if len(eligible) > 2 else None})

    overall_entries = tourney.get_leaderboard(round_num=None).get_entries()
    overall_eligible, overall_disqualified = _select_top_qualified(overall_entries, required=3)
    for record in overall_disqualified:
        disqualified_records.setdefault(record['player'], record)

    global_first = overall_eligible[0].player if len(overall_eligible) > 0 else None
    global_second = overall_eligible[1].player if len(overall_eligible) > 1 else None
    global_third = overall_eligible[2].player if len(overall_eligible) > 2 else None

    def resolve_entry(player):
        if not player:
            return None
        player_data = _player_data(player)
        xuid = tourney._resolve_player_xuid(player)
        return {'player': player, 'xuid': xuid, 'avatar': (player_data or {}).get('avatar', url_for('main.static', filename='img/head.png'))}

    recipients = {
        'round_firsts': [{ 'round': e['round'], **(resolve_entry(e['player']) or {'player': None, 'xuid': None}) } for e in round_firsts],
        'round_seconds': [{ 'round': e['round'], **(resolve_entry(e['player']) or {'player': None, 'xuid': None}) } for e in round_seconds],
        'round_thirds': [{ 'round': e['round'], **(resolve_entry(e['player']) or {'player': None, 'xuid': None}) } for e in round_thirds],
        'global': {
            'first': resolve_entry(global_first),
            'second': resolve_entry(global_second),
            'third': resolve_entry(global_third),
        },
    }

    validation_payload = {
        'disqualifiedPlayers': list(disqualified_records.values()),
        'recipients': recipients,
        'playerData': {
            player: {'avatar': data.get('avatar')}
            for player, data in player_data_cache.items()
            if isinstance(data, dict)
        },
    }

    from app import db
    archives = tourney.archives or {}
    if not isinstance(archives, dict):
        archives = {}
    archives['validation'] = validation_payload
    tourney.recipients_validated = True
    tourney.archives = archives
    try:
        db.session.add(tourney)
        db.session.commit()
    except Exception as exc:
        tourney.recipients_validated = False # Reset validation status on failure to allow retrying
        current_app.logger.exception('Failed to save validated recipients: %s', exc)
        return jsonify({'success': False, 'message': 'Failed to save recipients.'}), 500

    return jsonify({'success': True, 'message': 'Recipients validated and saved.', 'recipients': recipients, 'disqualifiedPlayers': validation_payload['disqualifiedPlayers']})

def _relative_logic(ts, now_ts):
    diff = ts - now_ts
    abs_diff = abs(diff)
    if abs_diff < 60: amount, unit = abs_diff, 's'
    elif abs_diff < 3600: amount, unit = abs_diff // 60, 'm'
    elif abs_diff < 86400: amount, unit = abs_diff // 3600, 'h'
    else: amount, unit = abs_diff // 86400, 'd'
    return 'now' if diff == 0 else (f'in {amount}{unit}' if diff > 0 else f'{amount}{unit} ago')

@main_bp.route('/scheduler/<int:tournament_id>/discord/send', methods=['POST'])
@login_required
def tournament_send_discord(tournament_id: int):
    if not current_user.is_manager():
        flash('Access denied: Managers only.', 'error')
        return redirect(url_for('main.tournament_editor', tournament_id=tournament_id)), 403

    tourney = Tournament.query.get_or_404(tournament_id)
    if tourney.tournament_info_discord_status or tourney.is_expired:
        # Prevents resending announcements and also ensures expired tournaments that missed the announcement window are still marked as having sent the announcement (since the announcement is locked after expiration)
        if not tourney.tournament_info_discord_status:
            tourney.tournament_info_discord_status = True
            from app import db
            db.session.commit()
        flash('Tournament announcement is locked and cannot be sent again.', 'error')
        return redirect(url_for('main.tournament_editor', tournament_id=tourney.id)), 403

    message = (tourney.tournament_info_discord_message or request.form.get('message') or '').strip()

    round_hours = max(0.5, round(tourney.round_duration / 3600, 1))
    round_hours_display = int(round_hours) if float(round_hours).is_integer() else round_hours
    message_content = (
        f"# :mega: {tourney.name} Announcement\n\n"
        f"This tournament will start on <t:{tourney.start_unix}:F> and will conclude <t:{tourney.end_unix}:R> on <t:{tourney.end_unix}:F>.\n\n"
        f"**Each round will last {round_hours_display} hour{'' if round_hours_display == 1 else 's'}.**"
    )

    if message: message_content += f"\n### Additional Info\n{message}\n"

    message_content += (
        "\nYour kills will automatically be counted and tracked at https://ngmc.co/tournament."
    )

    message_content += (
        "\n\n## :clipboard: Tournament Rules\n"
        "To recieve prizes, players must:\n"
        "- Not have active punishments during the tournament\n"
        f"- Have no bans within **30 days** of <t:{tourney.start_unix}:D>\n"
        f"- Have no mutes within **7 days** of <t:{tourney.start_unix}:D>\n"
        "- Follow our [Enforcement Policy](https://ngmc.co/enforcement)\n"
    )

    prizes = getattr(tourney, 'prizes', {}) or {}
    if any(prizes.get(key) for key in ['overall_first', 'overall_second', 'overall_third']):
        message_content += (
            "\n## :trophy: Overall Ranking Prizes\n"
            "-# Overall Ranking rewards the top 3 players across all rounds. Overall Ranking prizes are cumulative with Per-Round Rewards.\n"
            f"{format_prize_lines({
                'first': prizes.get('overall_first', 'TBA'),
                'second': prizes.get('overall_second', 'TBA'),
                'third': prizes.get('overall_third', 'TBA'),
            })}"
        )
    if any(prizes.get(key) for key in ['round_first', 'round_second', 'round_third']):
        message_content += (
            "\n## :gift: Per-Round Prizes\n"
            f"{format_prize_lines({
                'first': prizes.get('round_first', 'TBA'),
                'second': prizes.get('round_second', 'TBA'),
                'third': prizes.get('round_third', 'TBA'),
            })}"
            "\n-# Please note: Titan Rank rewards are not transferable and per-round Titan Rank rewards are non-cumulative; however, overall ranking players will receive their overall-ranking Titan prize on top of their round-ranking Titan prize (if applicable). All other rewards are cumulative across rounds and overall ranking."
        )
    message_content += (
        "\n\n We wish you all the best! Tournament rules and prizes are subject to change, with <@&1081345603268792410> posted here at <#1466960479485296640>."
        f"\n-# Viewing this message in the future? Visit our [unofficial Tournament Tracking website](https://ng-tournies.onrender.com{url_for('main.tournament_editor', tournament_id=tourney.id)}) for more info about this tournament!"
    )

    try:
        from discord_webhook.constants import MessageFlags
        response, ok = discord_send(
            location=ChannelWebhookUrl.ANNOUNCEMENT_WEBHOOK_URL,
            content=message_content,
            flags=MessageFlags.SUPPRESS_EMBEDS.value
        )
        if ok:
            tourney.tournament_info_discord_status = True
            from app import db
            db.session.commit()
            flash('Discord message sent', 'success')
        else:
            flash(f'Failed to send Discord message: {json.loads(response.content.decode("utf-8")).get("content", "Unknown error")}', 'error')
    except json.JSONDecodeError as exc:
        flash('Failed to send Discord message.', 'error')
        current_app.logger.exception(f"Discord response content was not valid JSON: {exc}")
    except Exception as exc:
        flash('Error sending Discord message. Please contact an administrator.', 'error')
        current_app.logger.exception(f"Unexpected error occurred while sending Discord message: {exc}")

    return redirect(url_for('main.tournament_editor', tournament_id=tourney.id))

@main_bp.route('/scheduler/<int:tournament_id>/prizes/send', methods=['POST'])
@login_required
def tournament_send_prizes_discord(tournament_id: int):
    if not current_user.is_manager():
        flash('Access denied: Managers only.', 'error')
        return redirect(url_for('main.tournament_editor', tournament_id=tournament_id)), 403

    tourney = Tournament.query.get_or_404(tournament_id)

    if not tourney.is_expired:
        flash('Prize announcement is locked until the tournament ends.', 'error')
        return redirect(url_for('main.tournament_editor', tournament_id=tourney.id)), 403

    if tourney.awards_distributed:
        flash('Prize announcement is locked and cannot be sent again.', 'error')
        return redirect(url_for('main.tournament_editor', tournament_id=tourney.id)), 403

    prizes = getattr(tourney, 'prizes', {}) or {}

    overall_prizes = {
        'first': prizes.get('overall_first', ''),
        'second': prizes.get('overall_second', ''),
        'third': prizes.get('overall_third', ''),
    }

    round_prizes = {
        'first': prizes.get('round_first', ''),
        'second': prizes.get('round_second', ''),
        'third': prizes.get('round_third', ''),
    }

    overall_entries = tourney.get_leaderboard(round_num=None).get_entries(limit=3)
    overall_text = format_placement_lines(overall_entries, label='kills!')

    round_sections = []
    for round_num in range(1, tourney.round_count + 1):
        entries = tourney.get_leaderboard(round_num=round_num).get_top(3)
        round_sections.append(
            f"### Round {round_num}\n{format_placement_lines(entries)}"
        )

    rounds_text = "\n".join(round_sections)

    optional_message = (tourney.tournament_info_discord_message or '').strip()

    message = (
        f"# :mega: {tourney.name} Rewards!\n\n"
    )

    if optional_message:
        message += f"{optional_message}\n\n"

    message += (
        f"## :trophy: **OVERALL WINNERS**\n\n"
        f"Congratulations to these players with the most kills across the board!\n\n"
        f"{overall_text}\n\n"

        f"## :bar_chart: **ROUND WINNERS**\n\n"
        f"{rounds_text}\n\n"

        f"## :gift: **PRIZES**\n"
        f"-# <@&1081345603268792410> Titan prizes have now been distributed, and can be claimed from the [Gift Inventory](https://ngmc.co/store). Other prizes will be distributed at a later date. Thank you for participating in the {tourney.name}!\n\n"

        "### Per-Round Prizes\n"
        f"{format_prize_lines(round_prizes)}\n\n"
        "### Overall Prizes\n"
        f"{format_prize_lines(overall_prizes)}\n\n"
        "Congratulations to all the winners! See you in the next tournament!"
        f"\n-# Viewing this message in the future? Visit our [unofficial Tournament Tracking website](https://ng-tournies.onrender.com{url_for('main.tournament_editor', tournament_id=tourney.id)}) for more details about this tournament!"
    )

    try:
        response, ok = discord_send(
            location=ChannelWebhookUrl.ANNOUNCEMENT_WEBHOOK_URL,
            content=message
        )
        if ok:
            tourney.awards_distributed = True
            from app import db
            db.session.commit()
            flash('Prize announcement sent', 'success')
        else:
            flash(f'Failed to send Discord message: {response.content.decode("utf-8")}', 'error')
    except json.JSONDecodeError as exc:
        flash('Failed to send Discord message: Invalid response from Discord.', 'error')
        current_app.logger.exception(f"Discord response content was not valid JSON: {exc}")
    except Exception as exc:
        flash('Error sending Discord message. Please contact an administrator.', 'error')
        current_app.logger.exception(f"Unexpected error occurred while sending Discord message: {exc}")

    return redirect(url_for('main.tournament_editor', tournament_id=tourney.id))

@main_bp.route('/scheduler/<int:tournament_id>/cache/stats', methods=['POST'])
def tournament_cache_stats(tournament_id: int):
    if not current_user:
        return jsonify({'success': False, 'message': 'Access denied. Please log in.'}), 403

    tourney = Tournament.query.get_or_404(tournament_id)
    current_app.logger.info(
        "Cache stats requested for tournament id=%s name=%s round_count=%s archived_rounds=%s",
        tourney.id,
        tourney.name,
        tourney.round_count,
        sorted((tourney.archives or {}).get('rounds', {}).keys()) if isinstance(tourney.archives, dict) else [],
    )

    def _round_keys() -> set[int]:
        archives = tourney.archives or {}
        if not isinstance(archives, dict):
            return set()
        rounds_data = archives.get('rounds', {})
        if not isinstance(rounds_data, dict):
            return set()
        return {int(key) for key in rounds_data.keys() if str(key).isdigit()}

    def _format_rounds(rounds: list[int], singular_label: str = 'round', plural_label: str = 'rounds') -> str:
        if not rounds:
            return ''
        if len(rounds) == 1:
            return f'{singular_label} {rounds[0]}'
        if len(rounds) == 2:
            return f'{plural_label} {rounds[0]} and {rounds[1]}'
        return f"{plural_label} {', '.join(map(str, rounds[:-1]))} and {rounds[-1]}"

    before_rounds = _round_keys()
    current_app.logger.debug(
        "Cache stats before archive attempt for tournament id=%s: round_keys=%s",
        tourney.id,
        sorted(before_rounds),
    )

    try:
        failures = list(tourney.archive_stats())
        current_app.logger.info(
            "Archive generator completed for tournament id=%s with results=%s",
            tourney.id,
            failures,
        )
        failed_rounds = sorted(round_num for round_num, ok in failures if ok is False)

        # Refresh to get updated archive data
        from app import db
        db.session.refresh(tourney)

        after_rounds = _round_keys()
        current_app.logger.debug(
            "Cache stats after archive attempt for tournament id=%s: round_keys=%s",
            tourney.id,
            sorted(after_rounds),
        )
        cached_rounds = sorted(after_rounds - before_rounds)

        if cached_rounds:
            flash(f"Successfully cached {_format_rounds(cached_rounds)}", 'success')
            return jsonify({'success': True})
        elif failed_rounds:
            message = f"Failed to cache {_format_rounds(failed_rounds)}"
            current_app.logger.error(
                "Cache stats failure for tournament id=%s: failed_rounds=%s before_rounds=%s after_rounds=%s",
                tourney.id,
                failed_rounds,
                sorted(before_rounds),
                sorted(after_rounds),
            )
            return jsonify({'success': False, 'message': message, 'cached_rounds': [], 'failed_rounds': failed_rounds}), 500
        else:
            message = 'No finished rounds available to cache.'
            current_app.logger.error(
                "Cache stats found no finished rounds for tournament id=%s: before_rounds=%s after_rounds=%s round_count=%s current_round=%s is_active=%s is_expired=%s",
                tourney.id,
                sorted(before_rounds),
                sorted(after_rounds),
                tourney.round_count,
                getattr(tourney, 'current_round', None),
                tourney.is_active,
                tourney.is_expired,
            )
    except TournamentArchiveException as exc:
        current_app.logger.error(
            "TournamentArchiveException while caching tournament id=%s: %s",
            tourney.id,
            exc,
        )
        message = f'Error archiving stats: {str(exc)}'
        return jsonify({'success': False, 'message': message, 'cached_rounds': [], 'failed_rounds': []}), 500
    except Exception:
        current_app.logger.exception('Error caching tournament stats')
        message = 'Unexpected error caching tournament stats'
        return jsonify({'success': False, 'message': message, 'cached_rounds': [], 'failed_rounds': []}), 500    

    return jsonify({'success': False, 'message': message, 'cached_rounds': [], 'failed_rounds': []}), 400

@main_bp.route('/scheduler/<int:tournament_id>/delete', methods=['POST'])
@login_required
def tournament_delete(tournament_id: int):
    from app import db
    if not current_user.is_manager():
        flash('Access denied: Managers only.', 'error')
        return redirect(url_for('main.tournament_editor', tournament_id=tournament_id)), 403

    tourney = Tournament.query.get_or_404(tournament_id)

    password = (request.form.get('confirm_password') or '').strip()
    if not password:
        flash('Please enter your password to confirm deletion.', 'error')
        return redirect(url_for('main.tournament_editor', tournament_id=tourney.id))

    # Verify current user's password
    try:
        if not current_user.check_password(password):
            flash('Password incorrect. Tournament not deleted.', 'error')
            return redirect(url_for('main.tournament_editor', tournament_id=tourney.id))
    except Exception:
        flash('Error verifying password. Tournament not deleted.', 'error')
        return redirect(url_for('main.tournament_editor', tournament_id=tourney.id))

    # Perform deletion
    try:
        db.session.delete(tourney)
        db.session.commit()
        current_app.logger.info('Tournament id=%s name=%s deleted by user id=%s', tourney.id, tourney.name, current_user.id)
        flash(f'Tournament "{tourney.name}" deleted.', 'success')
        return redirect(url_for('main.scheduler'))
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Failed to delete tournament id=%s', tourney.id)
        flash('Failed to delete tournament. Please contact an administrator.', 'error')
        return redirect(url_for('main.tournament_editor', tournament_id=tourney.id))

@main_bp.app_errorhandler(HTTPException)
def handle_http_exception(e):
    from app import db
    db.session.rollback()

    if e.code >= 500:
        current_app.logger.exception('HTTP error %s: %s', e.code, e)
    elif e.code >= 400:
        current_app.logger.debug('HTTP error %s: %s', e.code, e)

    try:
        description = e.description
        if e.code == 500:
            description = 'An unexpected error occurred. Please refresh the page and try again, or go back using the button below.'
        return render_template('error.html', error=description, code=e.code), e.code
    except Exception:
        current_app.logger.exception('Failed to render HTTP error page: %s', e)
        return f'Error {e.code}: {e.description}', e.code

@main_bp.app_errorhandler(Exception)
def handle_exception(e):
    from app import db
    db.session.rollback()

    def _is_retryable_db_error(error: Exception) -> bool:
        if not isinstance(error, OperationalError):
            return False

        message = str(error).lower()
        retryable_phrases = (
            'server closed the connection unexpectedly',
            'connection has been closed unexpectedly',
            'could not receive data from server',
            'connection refused',
            'terminating connection',
        )
        return any(phrase in message for phrase in retryable_phrases)

    reload_page = _is_retryable_db_error(e)

    if reload_page:
        current_app.logger.warning('Transient database connection error: %s', e)
    else:
        current_app.logger.exception('Unhandled exception: %s', e)

    try:
        error_message = (
            'The database connection dropped unexpectedly. Please refresh the page to reconnect.'
            if reload_page else
            'An unexpected error occurred.'
        )

        return render_template('error.html', error=error_message, code=500, reload_page=reload_page), 500
    except Exception:
        current_app.logger.exception('Failed to render error page after exception: %s', e)
        return 'Error 500: An unexpected error occurred.', 500

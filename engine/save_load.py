import os
import json
from engine.game_state import DatabaseManager, GameState, Character
from engine.config import config, GameConfig


def _generate_world_context(llm, rag, ws, language):
    # Query world_reference for this world's crawled text, then ask the LLM
    # to write a rich 2000-char overview.  Returns "" on any failure.
    refs = rag.retrieve_world_reference(ws['id'], ws['name'], n_results=6)
    ref_text = "\n".join(refs) if refs else ""
    system_msg = (
        f"You are a game master writing a world overview for a {ws['name']} campaign. "
        f"Return plain text only — no JSON, no headers."
    )
    user_msg = (
        f"Write a rich 2000-character overview of this world in {language}. "
        f"Cover: geography, factions, tone, major threats, daily life.\n"
        f"Reference material:\n{ref_text}\n"
        f"Base lore: {ws.get('world_lore', '')}"
    )
    try:
        text = llm._chat(
            messages=[{"role": "system", "content": system_msg},
                      {"role": "user",   "content": user_msg}],
            json_mode=False,
        ).strip()
        return text if len(text) > 50 else ""
    except Exception as e:
        print(f"[save_load] _generate_world_context failed: {e}")
        return ""


def _seed_world_rules(llm, rag, ws, language):
    # Skip if we already seeded rules for this world to keep the call idempotent.
    first_rule_id = f"world_{ws['id']}_0"
    try:
        existing = rag.rules_collection.get(ids=[first_rule_id], include=[])
        if existing['ids']:
            return
    except Exception:
        pass

    # Pull relevant reference text to ground the rule generation
    refs = rag.retrieve_world_reference(ws['id'], "rules mechanics social combat", n_results=4)
    ref_text = "\n".join(refs) if refs else ws.get('world_lore', ws['name'])

    user_msg = (
        f"Generate 6 world-specific game rules for {ws['name']}. "
        f"Each rule is a single sentence describing a mechanical or social rule "
        f"unique to this setting. "
        f"Return a JSON array of exactly 6 strings. "
        f"Reference: {ref_text}"
    )
    try:
        text = llm._chat(
            messages=[{"role": "system", "content": "Return a JSON array of strings only."},
                      {"role": "user",   "content": user_msg}],
            json_mode=True,
        ).strip()
        # Extract JSON array from the response (may have surrounding prose)
        start = text.find('[')
        end = text.rfind(']')
        if start == -1 or end == -1:
            return
        rules = json.loads(text[start:end + 1])
        if not isinstance(rules, list):
            return
        for i, rule_text in enumerate(rules[:8]):
            rule_id = f"world_{ws['id']}_{i}"
            rag.add_game_rule(str(rule_text), rule_id,
                              metadata={"type": "world_rule", "world_id": ws['id']})
    except Exception as e:
        print(f"[save_load] _seed_world_rules failed: {e}")


class SaveLoadManager:
    """Handles creating, saving, and loading game sessions (1-4 players)."""

    def __init__(self, db_manager=None):
        if db_manager is None:
            db_manager = DatabaseManager(config.get_db_path())
        self.db_manager = db_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_new_game(self, save_name, character_name, race, char_class,
                        appearance, personality,
                        difficulty="Normal", language="English",
                        world_context="", world_setting="dnd5e",
                        extra_players=None,
                        allow_custom_action=True,
                        gender="",
                        llm=None, rag=None):
        """
        Create a new game with 1-4 players.

        Parameters
        ----------
        save_name       : str  — unique identifier for this save file
        character_name  : str  — player 1 name
        race, char_class: str  — player 1 race / class
        appearance      : str  — player 1 appearance (image gen)
        personality     : str  — player 1 personality (LLM prompt)
        extra_players   : list[dict] | None
            Additional players (2-4). Each dict:
              {name, race, char_class, appearance, personality}
            Missing fields fall back to 'Human'/'Warrior'/''/'' defaults.

        Returns (party, game_state, session) on success, (None, None, None) on duplicate.
        party is a list[Character] ordered by turn index.
        """
        session = self.db_manager.get_session()

        existing = session.query(GameState).filter_by(save_name=save_name).first()
        if existing:
            session.close()
            return None, None, None

        ws = GameConfig.get_world_setting(world_setting)
        starting_location = ws['starting_location']
        effective_world_context = (
            world_context.strip() or ws.get('world_lore', 'A world waiting to be explored.')
        )

        # --- Build party config list ---
        player_configs = [{
            'name':        character_name,
            'race':        race,
            'char_class':  char_class,
            'appearance':  appearance,
            'personality': personality,
            'gender':      gender,
            'is_ai':       False,   # party leader is always human
        }]
        for ep in (extra_players or []):
            player_configs.append({
                'name':           ep.get('name', 'Adventurer'),
                'race':           ep.get('race', 'Human'),
                'char_class':     ep.get('char_class', 'Warrior'),
                'appearance':     ep.get('appearance', ''),
                'personality':    ep.get('personality', ''),
                'gender':         ep.get('gender', ''),
                'is_ai':          ep.get('is_ai', False),
                'ai_personality': ep.get('ai_personality', 'tactical'),
                'ai_difficulty':  ep.get('ai_difficulty', 'normal'),
            })
        player_configs = player_configs[:GameConfig.MAX_PARTY_SIZE]

        # --- Create Character rows using class-balanced base stats ---
        party = []
        for cfg in player_configs:
            base = GameConfig.CLASS_BASE_STATS.get(
                cfg['char_class'].lower(), GameConfig.CLASS_BASE_STATS['warrior']
            )
            char = Character(
                name=cfg['name'],
                race=cfg['race'],
                char_class=cfg['char_class'],
                gender=cfg.get('gender', ''),
                appearance=cfg['appearance'],
                personality=cfg['personality'],
                hp=base['hp'],       max_hp=base['max_hp'],
                mp=base['mp'],       max_mp=base['max_mp'],
                atk=base['atk'],     def_stat=base['def_stat'],
                mov=base['mov'],
                gold=base['gold'],
                inventory=[],
                skills=[],
            )
            session.add(char)
            party.append(char)
        session.commit()

        party_ids = [c.id for c in party]

        # Initial contribution tracker — one entry per character
        init_contributions = {
            str(cid): {
                'damage_dealt':       0,
                'healing_done':       0,
                'skill_checks_passed': 0,
                'turns_taken':        0,
            }
            for cid in party_ids
        }

        # AI config per slot (only for slots 1+ that opted in to AI control)
        init_ai_configs = {}
        for i, cfg in enumerate(player_configs):
            if cfg.get('is_ai', False) and i > 0:
                init_ai_configs[str(i)] = {
                    'is_ai':       True,
                    'personality': cfg.get('ai_personality', 'tactical'),
                    'difficulty':  cfg.get('ai_difficulty', 'normal'),
                }

        game_state = GameState(
            save_name=save_name,
            current_location=starting_location,
            world_context=effective_world_context,
            difficulty=difficulty,
            language=language,
            world_setting=world_setting,
            player_id=party_ids[0],     # party leader / backward-compat
            party_ids=party_ids,
            active_player_index=0,
            party_contributions=init_contributions,
            ai_configs=init_ai_configs,
            turn_count=0,
            relationships={},
            session_memory=[],
            known_entities={},
            allow_custom_action=1 if allow_custom_action else 0,
        )
        session.add(game_state)
        session.commit()

        # Clear story_events + world_lore so previous game's RAG context does not
        # bleed into this new game.  game_rules / world_reference are shared and kept.
        if rag:
            rag.reset_game_collections()

        # Auto-generate world_context from crawled reference data if not supplied
        if llm and rag and not world_context.strip():
            generated = _generate_world_context(llm, rag, ws, language)
            if generated:
                game_state.world_context = generated
                session.commit()

        # Seed world-specific mechanical/social rules into the shared game_rules RAG
        if llm and rag:
            _seed_world_rules(llm, rag, ws, language)

        return party, game_state, session

    def load_game(self, save_name):
        """
        Load an existing save.

        Returns (party, game_state, session) where party is a list[Character]
        ordered by party_ids.  (None, None, None) if save not found.
        """
        session = self.db_manager.get_session()
        game_state = session.query(GameState).filter_by(save_name=save_name).first()
        if not game_state:
            session.close()
            return None, None, None

        # Support saves created before multi-player (party_ids may be empty)
        party_ids = game_state.party_ids or []
        if not party_ids:
            party_ids = [game_state.player_id]

        party = []
        for cid in party_ids:
            char = session.query(Character).filter_by(id=cid).first()
            if char:
                party.append(char)

        if not party:
            session.close()
            return None, None, None

        return party, game_state, session

    def list_saves(self):
        """Return a list of save summaries for the load-game UI."""
        session = self.db_manager.get_session()
        saves = session.query(
            GameState.save_name,
            GameState.current_location,
            GameState.turn_count,
            GameState.party_ids,
        ).all()
        session.close()
        result = []
        for s in saves:
            party_ids = s[3] or []
            result.append({
                "save_name":   s[0],
                "location":    s[1],
                "turns":       s[2] or 0,
                "party_size":  len(party_ids) if party_ids else 1,
            })
        return result

    def delete_game(self, save_name):
        """
        Delete a save and its associated character data and all files in its directory.
        """
        import shutil
        session = self.db_manager.get_session()
        try:
            game_state = session.query(GameState).filter_by(save_name=save_name).first()
            if game_state:
                # Delete characters associated with this save
                party_ids = game_state.party_ids or []
                if not party_ids:
                    party_ids = [game_state.player_id]
                
                for cid in party_ids:
                    char = session.query(Character).filter_by(id=cid).first()
                    if char:
                        session.delete(char)
                
                # Delete the game state itself
                session.delete(game_state)
                session.commit()

                # Delete the entire save directory if it exists
                from engine.story_saver import get_save_dir
                save_dir = get_save_dir(save_name)
                if os.path.exists(save_dir):
                    shutil.rmtree(save_dir)
                
                return True
            return False
        except Exception as e:
            print(f"Error deleting save {save_name}: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_active_character(self, party, game_state):
        """Return the Character whose turn it is."""
        idx = (game_state.active_player_index or 0) % max(len(party), 1)
        return party[idx]

    def compute_end_game_rewards(self, party, game_state):
        """
        Split accumulated party gold among players based on weighted contribution scores.

        Score = (damage_dealt × 1.0 + healing_done × 1.5 + checks_passed × 20)
                × CLASS_BASE_STATS[class]['reward_weight']

        Returns {char.name: gold_share} — integer gold amounts that sum to party_gold.
        """
        contributions = game_state.party_contributions or {}
        total_party_gold = sum(c.gold for c in party)

        scores = {}
        for char in party:
            cdata = contributions.get(str(char.id), {})
            raw = (
                cdata.get('damage_dealt', 0) * 1.0
                + cdata.get('healing_done', 0) * 1.5
                + cdata.get('skill_checks_passed', 0) * 20.0
            )
            weight = GameConfig.CLASS_BASE_STATS.get(
                char.char_class.lower(), {}
            ).get('reward_weight', 1.0)
            scores[char.name] = max(raw * weight, 1.0)  # floor of 1 so nobody gets 0

        total_score = sum(scores.values())
        result = {}
        remainder = total_party_gold
        names = list(scores.keys())
        for i, name in enumerate(names):
            if i == len(names) - 1:
                result[name] = remainder   # give leftover to last to avoid rounding loss
            else:
                share = int(total_party_gold * scores[name] / total_score)
                result[name] = share
                remainder -= share
        return result

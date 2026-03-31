import random
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import and_, or_
from engine.game_state import GameState, EntityRelation

class WorldManager:
    def __init__(self, db_session, state_model: GameState):
        self.session = db_session
        self.state = state_model

    def update_location(self, new_location):
        self.state.current_location = new_location
        self.session.commit()

    def update_world_context(self, context_str):
        self.state.world_context = context_str
        self.session.commit()

    def update_relationship(self, entity_name, affinity_delta, state=None, goal=None,
                            emotion=None, action=None, health=None,
                            proper_name=None, aliases=None,
                            biography=None, personality=None, traits=None):
        """
        Update an NPC / faction relationship entry.

        Core fields (always available):
          affinity — signed integer (-100 = hostile, 0 = neutral, +100 = devoted)
          state    — short mood label (Friendly / Suspicious / Fearful / …)
          goal     — NPC's current short-term objective

        Rich profile fields (populated on first encounter, then stable):
          proper_name  — proper given name (may differ from the dict key if key is a title)
          aliases      — list of titles, honorifics, nicknames
          biography    — 2-3 sentence life history
          personality  — MBTI type + description
          traits       — appearance, build, intelligence, physique (text)

        Scene-volatile fields (updated each turn, cleared when NPC leaves):
          emotion — current emotional state (set only when NPC is in scene)
          action  — what the NPC is visibly doing right now
          health  — health status (Healthy / Wounded / …)

        Legacy flat-integer entries ({"Village Elder": 10}) are silently migrated.
        """
        rels = dict(self.state.relationships or {})
        existing = rels.get(entity_name, {})

        # Migrate legacy format
        if isinstance(existing, (int, float)):
            existing = {"affinity": int(existing), "state": "Neutral", "goal": ""}

        existing["affinity"] = max(-100, min(100, existing.get("affinity", 0) + affinity_delta))
        if state is not None:
            existing["state"] = state
        if goal is not None:
            existing["goal"] = goal
        if emotion is not None:
            existing["emotion"] = emotion
        if action is not None:
            existing["action"] = action
        if health is not None:
            existing["health"] = health
        if proper_name is not None:
            existing["proper_name"] = proper_name
        if aliases is not None:
            existing["aliases"] = aliases
        if biography is not None:
            existing["biography"] = biography
        if personality is not None:
            existing["personality"] = personality
        if traits is not None:
            existing["traits"] = traits

        rels[entity_name] = existing
        self.state.relationships = rels
        # flag_modified is required: SQLAlchemy cannot detect in-place mutations
        # to JSON columns without an explicit signal
        flag_modified(self.state, 'relationships')
        self.session.commit()

    def register_npc(self, display_name, profile):
        """
        Register a new NPC entry in relationships with a full generated profile.

        If display_name is already tracked, only fills in missing profile fields
        (never overwrites existing data — avoids clobbering turn-by-turn state).
        profile is a dict returned by LLMClient.generate_npc_profile().
        """
        rels = dict(self.state.relationships or {})
        if display_name in rels:
            # Already tracked — only back-fill missing profile fields
            existing = rels[display_name]
            if isinstance(existing, (int, float)):
                existing = {"affinity": int(existing), "state": "Neutral", "goal": ""}
            for field in ('proper_name', 'aliases', 'biography', 'personality', 'traits', 'health', 'action'):
                if not existing.get(field) and profile.get(field):
                    existing[field] = profile[field]
            if not existing.get('goal') and profile.get('goal'):
                existing['goal'] = profile['goal']
            rels[display_name] = existing
        else:
            rels[display_name] = {
                "affinity":    0,
                "state":       "Neutral",
                "goal":        profile.get('goal', ''),
                "proper_name": profile.get('proper_name', display_name),
                "aliases":     profile.get('aliases', []),
                "biography":   profile.get('biography', ''),
                "personality": profile.get('personality', ''),
                "traits":      profile.get('traits', ''),
                "health":      profile.get('health', 'Healthy'),
                "action":      profile.get('action', ''),
                "emotion":     "",   # emotion is empty until NPC enters an active scene
            }
        self.state.relationships = rels
        flag_modified(self.state, 'relationships')
        self.session.commit()

    def get_relationship(self, entity_name):
        """Return the relationship dict for an NPC, or neutral defaults if not tracked."""
        rels = self.state.relationships or {}
        entry = rels.get(entity_name, {"affinity": 0, "state": "Neutral", "goal": ""})
        if isinstance(entry, (int, float)):
            return {"affinity": int(entry), "state": "Neutral", "goal": ""}
        return entry

    # ------------------------------------------------------------------
    # Organization tracking
    # ------------------------------------------------------------------

    def register_organization(self, profile):
        """
        Register a new organization or back-fill missing fields on an existing one.

        profile must contain at minimum 'name'. All other fields default to ''.
        Keyed by name.lower() to allow case-insensitive deduplication.
        Never overwrites an already-populated field — safe to call on each turn.
        """
        orgs = dict(self.state.organizations or {})
        key  = profile.get('name', '').lower().strip()
        if not key:
            return

        existing = orgs.get(key, {})
        defaults = {
            'name':            profile.get('name', ''),
            'type':            '',
            'founder':         '',
            'history':         '',
            'member_count':    '',
            'current_leader':  '',
            'headquarters':    '',
            'alignment':       '',
            'description':     '',
            'first_seen_turn': profile.get('first_seen_turn', 0),
        }
        # Merge: fill defaults first, then overlay existing (non-empty) values,
        # then overlay new profile values into empty slots only.
        merged = {**defaults, **{k: v for k, v in existing.items() if v}}
        for field, value in profile.items():
            if value and not merged.get(field):
                merged[field] = value
        # Always preserve the earliest first_seen_turn
        if existing.get('first_seen_turn') is not None:
            merged['first_seen_turn'] = min(
                int(existing['first_seen_turn']),
                int(profile.get('first_seen_turn', merged['first_seen_turn'])),
            )
        orgs[key] = merged
        self.state.organizations = orgs
        flag_modified(self.state, 'organizations')
        self.session.commit()

    def get_organization(self, name):
        """Return the organization dict for name (case-insensitive), or None."""
        orgs = self.state.organizations or {}
        return orgs.get(name.lower().strip())

    def list_organizations(self):
        """Return a list of all organization dicts, sorted by first_seen_turn."""
        orgs = self.state.organizations or {}
        return sorted(orgs.values(), key=lambda o: o.get('first_seen_turn', 0))

    # ------------------------------------------------------------------
    # Entity relationship graph
    # ------------------------------------------------------------------

    def upsert_relation(self, source_type, source_key, target_type, target_key,
                        relation_type, strength=0, description='', since_turn=0):
        """
        Insert or update a directed relationship edge.

        If an edge with the same (game_state_id, source, target, relation_type)
        already exists, update strength, description and since_turn only when
        the new values are non-empty / stronger in magnitude.
        Returns the EntityRelation instance.
        """
        sk = source_key.lower().strip()
        tk = target_key.lower().strip()
        existing = (
            self.session.query(EntityRelation)
            .filter_by(
                game_state_id=self.state.id,
                source_type=source_type, source_key=sk,
                target_type=target_type, target_key=tk,
                relation_type=relation_type,
            )
            .first()
        )
        if existing:
            # Update only when new value carries more information
            if abs(strength) > abs(existing.strength):
                existing.strength = max(-100, min(100, strength))
            if description and not existing.description:
                existing.description = description
        else:
            existing = EntityRelation(
                game_state_id=self.state.id,
                source_type=source_type, source_key=sk,
                target_type=target_type, target_key=tk,
                relation_type=relation_type,
                strength=max(-100, min(100, strength)),
                description=description,
                since_turn=since_turn,
            )
            self.session.add(existing)
        self.session.commit()
        return existing

    def get_relations(self, entity_type, entity_key, direction='both'):
        """
        Return all EntityRelation rows involving the given entity.

        direction: 'outgoing' | 'incoming' | 'both'
        """
        ek = entity_key.lower().strip()
        src_filter = and_(
            EntityRelation.game_state_id == self.state.id,
            EntityRelation.source_type   == entity_type,
            EntityRelation.source_key    == ek,
        )
        tgt_filter = and_(
            EntityRelation.game_state_id == self.state.id,
            EntityRelation.target_type   == entity_type,
            EntityRelation.target_key    == ek,
        )
        if direction == 'outgoing':
            return self.session.query(EntityRelation).filter(src_filter).all()
        if direction == 'incoming':
            return self.session.query(EntityRelation).filter(tgt_filter).all()
        return self.session.query(EntityRelation).filter(or_(src_filter, tgt_filter)).all()

    def list_all_relations(self):
        """Return every EntityRelation row for this save, ordered by since_turn."""
        return (
            self.session.query(EntityRelation)
            .filter_by(game_state_id=self.state.id)
            .order_by(EntityRelation.since_turn)
            .all()
        )

    # ------------------------------------------------------------------
    # Dungeon map generation (room / corridor tree)
    # ------------------------------------------------------------------

    # Room type templates — (name_prefix, description_template)
    _ROOM_TYPES = [
        ('Entrance Hall',     'A grand but crumbling entrance. Torches flicker on damp stone walls.'),
        ('Guard Room',        'Overturned furniture and dried bloodstains hint at a recent struggle.'),
        ('Treasure Vault',    'Iron-banded chests line the walls. Some have been pried open.'),
        ('Altar Chamber',     'A defaced stone altar dominates the room. Dark stains mar its surface.'),
        ('Library',           'Rotting bookshelves hold crumbling tomes and scrolls.'),
        ('Prison Cell Block',  'Rusted iron bars separate rows of empty cells. Bones litter the floor.'),
        ('Alchemist\'s Lab',  'Shattered vials crunch underfoot. The air smells of sulfur and rot.'),
        ('Throne Room',       'A massive throne of black stone faces a corridor of collapsed pillars.'),
        ('Crypt',             'Stone sarcophagi line the walls. The air is cold and utterly still.'),
        ('Barracks',          'Crude bunks fill the room. Weapon racks stand mostly empty.'),
        ('Kitchen',           'A massive hearth, cold now, dominates this vaulted chamber.'),
        ('Ritual Circle',     'Strange runes are carved into the floor in a complex pattern.'),
    ]

    def generate_dungeon(self, room_count=8, seed=None):
        """
        Generate a random dungeon map using a depth-first spanning tree.

        Each room:
          id          — sequential string ('room_0', 'room_1', …)
          name        — descriptive room title
          description — short atmospheric text
          connections — list of adjacent room ids (bidirectional)
          enemies     — list of monster name strings (empty by default)
          loot        — list of item name strings (empty by default)
          visited     — bool, set True when player enters

        The map is stored in GameState.dungeon_map and committed.
        Returns the dict.
        """
        rng = random.Random(seed)
        room_count = max(3, min(room_count, 20))

        # Build rooms
        types_sample = rng.sample(
            self._ROOM_TYPES * (room_count // len(self._ROOM_TYPES) + 1),
            room_count,
        )
        rooms = {}
        for i in range(room_count):
            rid = f'room_{i}'
            name, desc = types_sample[i]
            rooms[rid] = {
                'id':          rid,
                'name':        name,
                'description': desc,
                'connections': [],
                'enemies':     [],
                'loot':        [],
                'visited':     False,
            }

        # Connect rooms using a random spanning tree (DFS order)
        room_ids = list(rooms.keys())
        connected = {room_ids[0]}
        remaining = set(room_ids[1:])
        while remaining:
            src = rng.choice(list(connected))
            tgt = rng.choice(list(remaining))
            rooms[src]['connections'].append(tgt)
            rooms[tgt]['connections'].append(src)
            connected.add(tgt)
            remaining.discard(tgt)

        # Add a few extra edges (loops) for variety — ~30 % of room_count
        extra = max(1, room_count // 3)
        for _ in range(extra):
            src, tgt = rng.sample(room_ids, 2)
            if tgt not in rooms[src]['connections']:
                rooms[src]['connections'].append(tgt)
                rooms[tgt]['connections'].append(src)

        # Mark the first room as visited (start position)
        rooms[room_ids[0]]['visited'] = True

        self.state.dungeon_map = rooms
        flag_modified(self.state, 'dungeon_map')
        self.session.commit()
        return rooms

    def get_adjacent_rooms(self, current_room_id=None):
        """
        Return list of adjacent room dicts for the given room id.
        If current_room_id is None, use current_location as key.
        """
        dungeon = self.state.dungeon_map or {}
        if not dungeon:
            return []
        if current_room_id is None:
            # Try to match current_location to a room name
            loc = (self.state.current_location or '').lower()
            for rid, room in dungeon.items():
                if room.get('name', '').lower() == loc:
                    current_room_id = rid
                    break
        if current_room_id not in dungeon:
            return []
        return [dungeon[cid] for cid in dungeon[current_room_id]['connections']
                if cid in dungeon]

    def mark_room_visited(self, room_id):
        """Mark a room as visited and commit."""
        dungeon = dict(self.state.dungeon_map or {})
        if room_id in dungeon:
            room = dict(dungeon[room_id])
            room['visited'] = True
            dungeon[room_id] = room
            self.state.dungeon_map = dungeon
            flag_modified(self.state, 'dungeon_map')
            self.session.commit()

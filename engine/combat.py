# engine/combat.py
# Advanced combat mechanics: class abilities, status effects, monster specials.
#
# Design principles (neuro-symbolic):
#   - ALL numeric outcomes (damage, healing, status DC saves) are deterministic
#     Python — never decided by the LLM.
#   - LLM only narrates the results it receives as hard facts.
#   - Status effects are stored in GameState.known_entities under each entity key
#     and in known_entities['_player_buffs'] for the player.

# ---------------------------------------------------------------------------
# XP & level progression (D&D 5e milestone style, scaled for short campaigns)
# ---------------------------------------------------------------------------
# Index = level - 1; value = total XP needed to reach that level.
LEVEL_XP_TABLE = [
    0,      # Lv 1 (start)
    300,    # Lv 2
    900,    # Lv 3
    2700,   # Lv 4
    6500,   # Lv 5
    14000,  # Lv 6
    23000,  # Lv 7
    34000,  # Lv 8
    48000,  # Lv 9
    64000,  # Lv 10 (cap)
]
MAX_LEVEL = len(LEVEL_XP_TABLE)


def xp_for_level(level):
    """Return cumulative XP required to reach `level` (1-based)."""
    idx = max(0, min(level - 1, MAX_LEVEL - 1))
    return LEVEL_XP_TABLE[idx]


def compute_level(total_xp):
    """Return the level (1..MAX_LEVEL) corresponding to total_xp."""
    level = 1
    for i, threshold in enumerate(LEVEL_XP_TABLE):
        if total_xp >= threshold:
            level = i + 1
    return min(level, MAX_LEVEL)


# ---------------------------------------------------------------------------
# Difficulty reward/penalty tables
# ---------------------------------------------------------------------------

# Per-difficulty multipliers and thresholds used by _grant_loot_and_xp()
# and _apply_death_penalty() in logic/events.py.
DIFFICULTY_REWARD = {
    'easy':   {'xp_mult': 0.75, 'loot_chance': 0.35},
    'normal': {'xp_mult': 1.00, 'loot_chance': 0.50},
    'hard':   {'xp_mult': 1.50, 'loot_chance': 0.65},
    'deadly': {'xp_mult': 2.00, 'loot_chance': 0.80},
}

# Death penalties applied when character.hp <= 0.
# gold_loss_pct  — fraction of current gold lost (0.0 = none)
# xp_loss_pct   — fraction of XP above the current level floor lost (0.0 = none)
#                 capped so the player can never lose a level
# drop_item      — if True, one random inventory item is dropped
DIFFICULTY_DEATH_PENALTY = {
    'easy':   {'gold_loss_pct': 0.00, 'xp_loss_pct': 0.00, 'drop_item': False},
    'normal': {'gold_loss_pct': 0.10, 'xp_loss_pct': 0.00, 'drop_item': False},
    'hard':   {'gold_loss_pct': 0.25, 'xp_loss_pct': 0.05, 'drop_item': False},
    'deadly': {'gold_loss_pct': 0.50, 'xp_loss_pct': 0.10, 'drop_item': True},
}


# ---------------------------------------------------------------------------
# Per-tier gold drop ranges (min, max) — difficulty multiplier applied on top
# ---------------------------------------------------------------------------
_TIER_GOLD_RANGES = {
    1: (2,  12),   # Easy:   scraps from lesser foes
    2: (8,  30),   # Normal: worthwhile spoils
    3: (25, 80),   # Hard:   significant treasure
    4: (80, 300),  # Deadly: fragment of a boss hoard
}


def roll_combat_gold(entity_entry, dice_roller, difficulty='normal'):
    """
    Roll a gold drop for a defeated enemy.
    Constructs (golems, animated objects) carry no coin.
    Amount is tier-based and scaled by the difficulty XP multiplier.
    Returns an integer >= 0.
    """
    if entity_entry.get('construct'):
        return 0
    tier      = entity_entry.get('tier', 1)
    gold_min, gold_max = _TIER_GOLD_RANGES.get(tier, (2, 12))
    spread    = max(1, gold_max - gold_min)
    rolled    = dice_roller.roll(f'1d{spread}')[2]   # 1 .. spread
    base      = gold_min - 1 + rolled                # gold_min .. gold_max
    diff_key  = (difficulty or 'normal').lower()
    xp_mult   = DIFFICULTY_REWARD.get(diff_key, DIFFICULTY_REWARD['normal'])['xp_mult']
    return max(0, int(base * xp_mult))


def roll_loot(entity_entry, dice_roller, difficulty='normal'):
    """
    Roll for loot from a defeated monster's loot table.
    Returns a list of item name strings (may be empty).
    Drop chance per item scales with difficulty (35 % easy … 80 % deadly).
    """
    loot_table = entity_entry.get('loot', [])
    if not loot_table:
        return []
    diff_key = (difficulty or 'normal').lower()
    chance   = DIFFICULTY_REWARD.get(diff_key, DIFFICULTY_REWARD['normal'])['loot_chance']
    # Convert chance to a 1d100 threshold (e.g. 0.65 → roll ≤ 65)
    threshold = int(chance * 100)
    dropped = []
    for item in loot_table:
        if dice_roller.roll('1d100')[2] <= threshold:
            dropped.append(item)
    return dropped

# ---------------------------------------------------------------------------
# Class combat abilities
# ---------------------------------------------------------------------------
# mp_cost      — MP spent when ability is used.
# hit_penalty  — subtracted from attack_total (trade accuracy for power).
# auto_hit     — skips the attack roll entirely (always hits).
# bonus_damage_dice — extra dice rolled on a hit (added to base weapon damage).
# heal_dice    — dice rolled for self-healing (not an attack).
# ignores_defense — net_damage skips the target's DEF // 2 reduction.
# aoe          — hits ALL living enemies (combat_result gains 'aoe': True).
# apply_status — status effect key applied to the target on a hit.
# once_per_combat — can only be used once per encounter.
# affects_undead_only — effect only applies to undead enemies.
# fear_dc      — DC for a Wisdom save; failure means enemy flees.

CLASS_ABILITIES = {
    'warrior': {
        'power_attack': {
            'name': 'Power Attack', 'cn_name': '強力攻擊',
            'keywords_en': ['power attack', 'power strike', 'heavy strike'],
            'keywords_cn': ['強力攻擊', '猛力打擊', '重擊'],
            'mp_cost': 0, 'hit_penalty': -4, 'bonus_damage_dice': '1d6',
            'description': 'Trade accuracy for devastating power (+1d6 damage, -4 to hit).',
        },
        'cleave': {
            'name': 'Cleave', 'cn_name': '橫掃',
            'keywords_en': ['cleave', 'sweep', 'wide swing', 'sweep attack'],
            'keywords_cn': ['橫掃', '掃擊', '範圍攻擊'],
            'mp_cost': 2, 'aoe': True,
            'description': 'Sweep weapon in an arc, hitting all nearby enemies (costs 2 MP).',
        },
        'second_wind': {
            'name': 'Second Wind', 'cn_name': '鬥志再燃',
            'keywords_en': ['second wind', 'recover', 'catch breath', 'rally'],
            'keywords_cn': ['鬥志再燃', '恢復鬥志', '士氣回復'],
            'mp_cost': 0, 'heal_dice': '1d10', 'once_per_combat': True,
            'description': 'Recover 1d10 HP from inner reserve (once per encounter).',
        },
    },
    'mage': {
        'magic_missile': {
            'name': 'Magic Missile', 'cn_name': '魔法飛彈',
            'keywords_en': ['magic missile', 'force missile', 'magic bolt'],
            'keywords_cn': ['魔法飛彈', '魔彈', '力量飛彈'],
            'mp_cost': 1, 'auto_hit': True, 'bonus_damage_dice': '1d4',
            'description': 'Auto-hitting force projectile that never misses (costs 1 MP).',
        },
        'fireball': {
            'name': 'Fireball', 'cn_name': '火球術',
            'keywords_en': ['fireball', 'fire ball', 'cast fire', 'throw fireball'],
            'keywords_cn': ['火球術', '火球', '爆炎球'],
            'mp_cost': 3, 'bonus_damage_dice': '3d6', 'aoe': True, 'apply_status': 'burning',
            'description': 'Explosive sphere of fire dealing 3d6 AoE damage with burning (3 MP).',
        },
        'arcane_shield': {
            'name': 'Arcane Shield', 'cn_name': '奧術護盾',
            'keywords_en': ['arcane shield', 'magic shield', 'mage shield', 'ward'],
            'keywords_cn': ['奧術護盾', '魔法護盾', '法術護壁'],
            'mp_cost': 2, 'heal_dice': None, 'def_bonus': 4, 'duration': 1,
            'description': 'Magical barrier grants +4 DEF until next hit is absorbed (2 MP).',
        },
        'frost_ray': {
            'name': 'Frost Ray', 'cn_name': '寒冰射線',
            'keywords_en': ['frost ray', 'ice ray', 'freezing ray', 'cast frost'],
            'keywords_cn': ['寒冰射線', '冰霜射線', '冰箭'],
            'mp_cost': 2, 'bonus_damage_dice': '1d6', 'apply_status': 'slowed',
            'description': 'Ray of ice dealing bonus 1d6 cold and slowing the target (2 MP).',
        },
    },
    'rogue': {
        'backstab': {
            'name': 'Backstab', 'cn_name': '背刺',
            'keywords_en': ['backstab', 'sneak attack', 'ambush', 'stab from behind'],
            'keywords_cn': ['背刺', '偷襲', '暗殺', '從背後刺'],
            'mp_cost': 0, 'bonus_damage_dice': '2d6',
            'requires_stealth': True,
            'description': 'Strike from shadow for +2d6 bonus damage (requires stealth/surprise).',
        },
        'evasion': {
            'name': 'Evasion', 'cn_name': '閃避',
            'keywords_en': ['evade', 'dodge', 'sidestep', 'acrobatic dodge'],
            'keywords_cn': ['閃避', '迴避', '躲避', '快速閃躲'],
            'mp_cost': 1, 'damage_reduction': 0.5,
            'description': 'Agile dodge halves the next incoming damage (1 MP).',
        },
        'poison_blade': {
            'name': 'Poison Blade', 'cn_name': '毒刃',
            'keywords_en': ['poison blade', 'poison attack', 'coat blade', 'envenom'],
            'keywords_cn': ['毒刃', '毒刀', '淬毒', '塗毒'],
            'mp_cost': 1, 'bonus_damage_dice': '1d4', 'apply_status': 'poisoned',
            'description': 'Coat weapon in poison: +1d4 damage, inflicts poisoned status (1 MP).',
        },
    },
    'cleric': {
        'divine_smite': {
            'name': 'Divine Smite', 'cn_name': '神聖打擊',
            'keywords_en': ['divine smite', 'holy strike', 'smite', 'sacred strike'],
            'keywords_cn': ['神聖打擊', '神聖重擊', '聖擊', '天罰'],
            'mp_cost': 2, 'bonus_damage_dice': '1d8', 'bonus_vs_undead_dice': '2d8',
            'description': 'Channel divine power for +1d8 radiant damage (2d8 vs undead, 2 MP).',
        },
        'turn_undead': {
            'name': 'Turn Undead', 'cn_name': '驅散不死生物',
            'keywords_en': ['turn undead', 'banish undead', 'holy turn', 'repel undead'],
            'keywords_cn': ['驅散不死', '驅逐不死', '神聖驅除', '聖光驅散'],
            'mp_cost': 2, 'affects_undead_only': True, 'fear_dc': 14,
            'description': 'Radiate holy power; undead enemies must flee DC 14 or be destroyed (2 MP).',
        },
        'healing_word': {
            'name': 'Healing Word', 'cn_name': '治癒之語',
            'keywords_en': ['healing word', 'heal', 'cure wounds', 'mend', 'restore'],
            'keywords_cn': ['治癒之語', '治癒語', '治療詞', '聖言治療'],
            'mp_cost': 2, 'heal_dice': '2d4',
            'description': 'Spoken prayer that mends wounds for 2d4 HP (2 MP).',
        },
        'sacred_flame': {
            'name': 'Sacred Flame', 'cn_name': '聖焰',
            'keywords_en': ['sacred flame', 'holy fire', 'divine fire', 'radiant flame'],
            'keywords_cn': ['聖焰', '神聖火焰', '天火', '聖光烈焰'],
            'mp_cost': 2, 'bonus_damage_dice': '1d8', 'ignores_defense': True,
            'description': 'Radiant flame deals +1d8 damage ignoring armour (2 MP).',
        },
    },
}

# ---------------------------------------------------------------------------
# Status effect definitions
# ---------------------------------------------------------------------------
# damage_per_turn — dice notation rolled at turn start.
# duration        — turns the effect lasts.
# skip_turn       — entity loses its action.
# attack_penalty  — subtracted from attack roll.
# heal_reduction  — multiplier on incoming healing (0.5 = half).
# suppress_regen  — if True, suppresses Regeneration ability.

STATUS_EFFECTS = {
    'poisoned': {
        'name': 'Poisoned', 'cn_name': '中毒',
        'damage_per_turn': '1d4', 'duration': 3,
        'description': 'Loses HP each turn from spreading poison.',
    },
    'burning': {
        'name': 'Burning', 'cn_name': '燃燒',
        'damage_per_turn': '1d6', 'duration': 2,
        'suppress_regen': True,
        'description': 'On fire; takes 1d6 fire damage per turn.',
    },
    'stunned': {
        'name': 'Stunned', 'cn_name': '昏迷',
        'skip_turn': True, 'duration': 1,
        'description': 'Cannot act this turn.',
    },
    'slowed': {
        'name': 'Slowed', 'cn_name': '緩慢',
        'attack_penalty': -2, 'duration': 2,
        'description': 'Movement and reactions are impaired (-2 to attacks).',
    },
    'bleeding': {
        'name': 'Bleeding', 'cn_name': '流血',
        'damage_per_turn': '1d4', 'duration': 3,
        'heal_reduction': 0.5,
        'description': 'Open wounds bleed each turn; healing is halved.',
    },
    'charmed': {
        'name': 'Charmed', 'cn_name': '魅惑',
        'skip_turn': True, 'duration': 1,
        'description': 'Entranced; skips one action.',
    },
    'feared': {
        'name': 'Feared', 'cn_name': '恐懼',
        'skip_turn': True, 'duration': 2,
        'description': 'Overcome by terror; retreats rather than acts.',
    },
    'weakened': {
        'name': 'Weakened', 'cn_name': '虛弱',
        'attack_penalty': -2, 'duration': 2,
        'description': 'Strength drained; -2 to attack rolls.',
    },
}

# ---------------------------------------------------------------------------
# CombatEngine
# ---------------------------------------------------------------------------

class CombatEngine:
    """
    Enhanced deterministic combat resolver.

    All numeric outcomes are computed here using DiceRoller.
    The LLM receives results as hard facts and only narrates them.
    """

    def __init__(self, dice_roller):
        self.dice = dice_roller

    # ------------------------------------------------------------------
    # Main attack resolution
    # ------------------------------------------------------------------

    def resolve_attack(self, character, char_logic, target_name,
                       current_state, class_ability_key=None):
        """
        Resolve a player attack, optionally enhanced by a class ability.

        Returns a combat_result dict consumed by EventManager and the LLM.
        Fields beyond the base dict:
          class_ability     — ability name used (or None)
          ability_bonus_dmg — bonus dice damage from ability
          ability_auto_hit  — True if attack cannot miss
          ability_aoe       — True if ability hits all enemies
          status_applied    — status effect key applied to target
          aoe_targets       — list of names hit by AoE (populated by caller)
        """
        from data.monsters import get_monster_by_name

        known       = current_state.known_entities or {}
        target_key  = target_name.lower()
        target_entry = known.get(target_key, {})
        target_def  = target_entry.get('def_stat', 10)

        # Resolve ability metadata
        ability_def = self._get_class_ability(character, class_ability_key)

        # Check MP
        mp_cost = ability_def.get('mp_cost', 0) if ability_def else 0
        if mp_cost > 0 and character.mp < mp_cost:
            ability_def = None  # not enough MP — fall back to normal attack
            mp_cost = 0

        # Apply hit penalty from ability (Power Attack etc.)
        hit_penalty = ability_def.get('hit_penalty', 0) if ability_def else 0
        auto_hit    = ability_def.get('auto_hit', False) if ability_def else False

        atk_modifier  = (character.atk - 10) // 2 + hit_penalty
        # Add ATK buff from active elixirs
        from engine.character import CharacterLogic as _CL
        atk_modifier += _CL.get_buff_modifier(current_state, 'atk')
        raw_d20       = self.dice.roll('1d20')[2]
        attack_total  = raw_d20 + atk_modifier
        critical      = raw_d20 == 20
        hit           = auto_hit or critical or (attack_total >= target_def)

        # Apply berserk ATK bonus if target is berserk'd (entity status)
        if target_entry.get('berserk_active'):
            target_def += target_entry.get('berserk_def_bonus', 0)

        # Damage calculation
        damage_notation = char_logic.get_weapon_damage_notation()
        raw_damage = 0
        net_damage = 0
        entity_hp_remaining = None
        ability_bonus_dmg = 0

        if hit:
            rolls, mod, total = self.dice.roll(damage_notation)
            if critical:
                dice_sum = sum(rolls)
                raw_damage = dice_sum * 2 + mod
            else:
                raw_damage = total

            # Ability bonus damage
            if ability_def and ability_def.get('bonus_damage_dice'):
                bonus_notation = ability_def['bonus_damage_dice']
                # Extra dice for undead targets (e.g. divine smite)
                is_undead = target_entry.get('undead', False)
                if is_undead and ability_def.get('bonus_vs_undead_dice'):
                    bonus_notation = ability_def['bonus_vs_undead_dice']
                ab_rolls, ab_mod, ab_total = self.dice.roll(bonus_notation)
                ability_bonus_dmg = ab_total
                raw_damage += ability_bonus_dmg

            # DEF reduction (sacred_flame ignores it)
            ignores_def = ability_def.get('ignores_defense', False) if ability_def else False
            if ignores_def:
                net_damage = raw_damage
            else:
                # Apply stone_skin passive (flat reduction)
                stone_skin = self._get_passive_reduction(target_entry)
                net_damage = max(0, raw_damage - (target_def // 2) - stone_skin)
                # Note: target_def here is the ENEMY's DEF — player buffs apply in counter-attack

            # Update entity HP in known_entities
            entity_hp_remaining = target_entry.get('hp')
            if entity_hp_remaining is not None:
                entity_hp_remaining = max(0, entity_hp_remaining - net_damage)

        # Status effect on hit
        status_applied = None
        if hit and ability_def and ability_def.get('apply_status'):
            status_applied = ability_def['apply_status']

        # Determine if this is an AoE ability
        is_aoe = bool(ability_def and ability_def.get('aoe')) if ability_def else False

        return {
            'target':               target_name,
            'target_def':           target_def,
            'atk_modifier':         atk_modifier,
            'attack_roll':          raw_d20,
            'attack_total':         attack_total,
            'critical':             critical,
            'hit':                  hit,
            'damage_notation':      damage_notation,
            'raw_damage':           raw_damage,
            'net_damage':           net_damage,
            'entity_hp_remaining':  entity_hp_remaining,
            # ability-specific
            'class_ability':        ability_def.get('name') if ability_def else None,
            'class_ability_key':    class_ability_key,
            'ability_bonus_dmg':    ability_bonus_dmg,
            'ability_auto_hit':     auto_hit,
            'ability_aoe':          is_aoe,
            'ability_mp_cost':      mp_cost,
            'status_applied':       status_applied,
            'aoe_targets':          [],
        }

    # ------------------------------------------------------------------
    # Non-attack class abilities (healing, shielding, turn undead)
    # ------------------------------------------------------------------

    def resolve_utility_ability(self, character, char_logic, target_name,
                                current_state, class_ability_key):
        """
        Resolve non-attack class abilities:
          - Second Wind / Healing Word  → returns hp_healed
          - Arcane Shield               → returns def_bonus applied to player
          - Turn Undead                 → returns fled_enemies list
          - Evasion                     → returns damage_reduction flag

        Returns a utility_result dict.
        """
        ability_def = self._get_class_ability(character, class_ability_key)
        if not ability_def:
            return {}

        mp_cost = ability_def.get('mp_cost', 0)
        if mp_cost > 0 and character.mp < mp_cost:
            return {'error': 'Not enough MP', 'mp_required': mp_cost}

        result = {
            'ability_name':  ability_def.get('name', class_ability_key),
            'ability_key':   class_ability_key,
            'mp_cost':       mp_cost,
            'hp_healed':     0,
            'def_bonus':     0,
            'fled_enemies':  [],
            'damage_reduction': 0.0,
        }

        # Healing abilities
        heal_dice = ability_def.get('heal_dice')
        if heal_dice:
            result['hp_healed'] = self.dice.roll(heal_dice)[2]

        # Arcane Shield
        if ability_def.get('def_bonus'):
            result['def_bonus'] = ability_def['def_bonus']

        # Evasion
        if ability_def.get('damage_reduction'):
            result['damage_reduction'] = ability_def['damage_reduction']

        # Turn Undead
        if ability_def.get('affects_undead_only') and ability_def.get('fear_dc'):
            known = current_state.known_entities or {}
            dc    = ability_def['fear_dc']
            fled  = []
            for key, entry in known.items():
                if key.startswith('_'):
                    continue
                if entry.get('alive', True) and entry.get('undead', False):
                    save_roll = self.dice.roll('1d20')[2]
                    if save_roll < dc:
                        fled.append(key)
            result['fled_enemies'] = fled

        return result

    # ------------------------------------------------------------------
    # Enemy counter-attack
    # ------------------------------------------------------------------

    def resolve_enemy_counter_attack(self, target_entry, character):
        """
        Resolve an enemy's counter-attack after the player hits it.

        Uses the monster's actual damage_dice if stored in known_entities,
        otherwise falls back to 1d6.  Applies the player's DEF reduction.
        Returns raw_damage (before DEF); CharacterLogic.take_damage() reduces it.
        """
        enemy_atk     = target_entry.get('atk', 5)
        enemy_atk_mod = (enemy_atk - 10) // 2
        enemy_roll    = self.dice.roll('1d20')[2]
        hit = (enemy_roll == 20) or (enemy_roll + enemy_atk_mod >= character.def_stat)
        if not hit:
            return {'hit': False, 'raw_damage': 0, 'roll': enemy_roll}

        # Use stored damage_dice or fallback
        dmg_notation = target_entry.get('damage_dice', '1d6')
        # Ensure ATK modifier is included
        if '+' not in dmg_notation and enemy_atk_mod > 0:
            dmg_notation = f"{dmg_notation}+{enemy_atk_mod}"

        # Check for special ability triggers on counter
        special_key = target_entry.get('special_ability')
        bonus_dmg   = 0
        status_applied = None

        if special_key == 'multiattack_2':
            # Second attack at half modifier
            _, _, extra = self.dice.roll(dmg_notation)
            bonus_dmg = max(0, extra)
        elif special_key == 'multiattack_3':
            _, _, e1 = self.dice.roll(dmg_notation)
            _, _, e2 = self.dice.roll(dmg_notation)
            bonus_dmg = max(0, e1) + max(0, e2)

        # On-hit status effects from special abilities
        on_hit_status = {
            'paralyzing_touch': 'stunned',
            'life_drain':       None,   # handled separately (HP drain)
            'disease_bite':     'poisoned',
            'venomous_sting':   'poisoned',
            'cursed_bite':      'bleeding',
            'strength_drain':   'weakened',
            'luring_song':      'charmed',
        }
        if special_key in on_hit_status:
            status_applied = on_hit_status[special_key]

        # Regeneration life-steal (wight / vampire_spawn)
        lifesteal = 0
        if special_key == 'life_drain':
            _, _, drain = self.dice.roll('1d6')
            lifesteal = drain

        raw_hit, _, total = self.dice.roll(dmg_notation)
        raw_damage = total + bonus_dmg

        return {
            'hit':           True,
            'roll':          enemy_roll,
            'raw_damage':    raw_damage,
            'status_applied': status_applied,
            'lifesteal':     lifesteal,
        }

    # ------------------------------------------------------------------
    # Flee mechanics
    # ------------------------------------------------------------------

    def resolve_flee(self, character, current_state, smoke_bonus=0):
        """
        Resolve a player's attempt to flee from combat.

        Flee roll: 1d20 + MOV modifier  vs  DC = 10 + highest living enemy MOV modifier.
        smoke_bonus — reduces flee DC (e.g. 4 when a smoke bomb was used this turn).
        Success → fled=True  (caller clears in_combat).
        Failure → fled=False + enemy free counter-attack result included.

        Returns:
            {
                'fled':         bool,
                'flee_roll':    int,    # raw d20
                'mov_modifier': int,
                'flee_total':   int,
                'flee_dc':      int,
                'smoke_bonus':  int,
                'counter':      dict | None,   # enemy counter on failure
                'damage_taken': int,           # damage from failed-flee counter
            }
        """
        mov_modifier = (character.mov - 10) // 2
        raw_roll, _, flee_total = self.dice.roll(f'1d20{"+" if mov_modifier >= 0 else ""}{mov_modifier}')

        # Determine DC from the fastest (highest MOV) living enemy
        known = current_state.known_entities or {}
        living_entries = [
            e for k, e in known.items()
            if not k.startswith('_') and e.get('alive', True)
        ]
        if living_entries:
            enemy_mov = max(e.get('mov', 10) for e in living_entries)
        else:
            enemy_mov = 10
        flee_dc = max(5, 10 + (enemy_mov - 10) // 2 - smoke_bonus)

        fled = flee_total >= flee_dc

        counter = None
        damage_taken = 0
        if not fled and living_entries:
            # Fastest enemy gets a free counter-attack on failed flee
            punisher = max(living_entries, key=lambda e: e.get('mov', 10))
            counter = self.resolve_enemy_counter_attack(punisher, character)
            if counter.get('hit'):
                raw_dmg = counter.get('raw_damage', 0)
                from engine.character import CharacterLogic as _CL
                buff_def = _CL.get_buff_modifier(current_state, 'def_stat')
                damage_taken = max(0, raw_dmg - ((character.def_stat + buff_def) // 2))

        return {
            'fled':         fled,
            'flee_roll':    raw_roll,
            'mov_modifier': mov_modifier,
            'flee_total':   flee_total,
            'flee_dc':      flee_dc,
            'smoke_bonus':  smoke_bonus,
            'counter':      counter,
            'damage_taken': damage_taken,
        }

    # ------------------------------------------------------------------
    # Status effect management
    # ------------------------------------------------------------------

    def apply_status_to_entity(self, entity_key, status_key, current_state):
        """
        Add or refresh a status effect on a known_entity.
        Stores status_effects list in known_entities[entity_key]['status_effects'].
        """
        if status_key not in STATUS_EFFECTS:
            return
        known = dict(current_state.known_entities or {})
        entry = dict(known.get(entity_key, {}))
        effects = list(entry.get('status_effects', []))

        # Replace existing same effect
        effects = [e for e in effects if e.get('key') != status_key]
        effects.append({
            'key':            status_key,
            'name':           STATUS_EFFECTS[status_key]['cn_name'],
            'turns_remaining': STATUS_EFFECTS[status_key]['duration'],
        })
        entry['status_effects'] = effects
        known[entity_key] = entry
        current_state.known_entities = known

    def tick_entity_status_effects(self, entity_key, current_state):
        """
        Process an entity's status effects at turn start.
        Returns {'damage': int, 'atk_penalty': int, 'skip_turn': bool,
                 'expired': [str], 'active': [str]}
        """
        known = dict(current_state.known_entities or {})
        entry = dict(known.get(entity_key, {}))
        effects = list(entry.get('status_effects', []))
        if not effects:
            return {'damage': 0, 'atk_penalty': 0, 'skip_turn': False,
                    'expired': [], 'active': []}

        total_damage  = 0
        atk_penalty   = 0
        skip_turn     = False
        expired       = []
        still_active  = []

        for eff in effects:
            key  = eff.get('key', '')
            defn = STATUS_EFFECTS.get(key, {})
            if not defn:
                continue

            # Apply effect
            dmg_notation = defn.get('damage_per_turn')
            if dmg_notation:
                total_damage += self.dice.roll(dmg_notation)[2]
            if defn.get('skip_turn'):
                skip_turn = True
            atk_penalty += defn.get('attack_penalty', 0)

            # Countdown
            eff = dict(eff)
            eff['turns_remaining'] = eff.get('turns_remaining', 1) - 1
            if eff['turns_remaining'] <= 0:
                expired.append(key)
            else:
                still_active.append(eff)

        # Regeneration — suppressed by burning
        regen_ability = entry.get('special_ability')
        from data.monsters import SPECIAL_ABILITIES
        regen_def = SPECIAL_ABILITIES.get(regen_ability or '')
        if (regen_def and regen_def.get('effect') == 'heal_per_turn'
                and 'burning' not in [e.get('key') for e in effects]):
            heal_val = regen_def.get('value', 0)
            current_hp = entry.get('hp', 0)
            max_hp     = entry.get('max_hp', current_hp)
            entry['hp'] = min(max_hp, current_hp + heal_val)
            total_damage = max(0, total_damage - heal_val)

        # Apply damage to entity HP
        if total_damage > 0:
            entry['hp'] = max(0, entry.get('hp', 0) - total_damage)
            if entry['hp'] <= 0:
                entry['alive'] = False

        entry['status_effects'] = still_active
        known[entity_key] = entry
        current_state.known_entities = known

        return {
            'damage':      total_damage,
            'atk_penalty': atk_penalty,
            'skip_turn':   skip_turn,
            'expired':     expired,
            'active':      [e['key'] for e in still_active],
        }

    def apply_status_to_player(self, status_key, current_state):
        """Add or refresh a player status effect stored in known_entities['_player_buffs']."""
        if status_key not in STATUS_EFFECTS:
            return
        known   = dict(current_state.known_entities or {})
        buffs   = list(known.get('_player_buffs', []))
        buffs   = [b for b in buffs if b.get('key') != status_key]
        buffs.append({
            'key':            status_key,
            'name':           STATUS_EFFECTS[status_key]['cn_name'],
            'turns_remaining': STATUS_EFFECTS[status_key]['duration'],
        })
        known['_player_buffs'] = buffs
        current_state.known_entities = known

    def tick_player_status_effects(self, current_state):
        """
        Process player status effects at the start of the player's turn.
        Returns same structure as tick_entity_status_effects.
        """
        known  = dict(current_state.known_entities or {})
        buffs  = list(known.get('_player_buffs', []))
        if not buffs:
            return {'damage': 0, 'atk_penalty': 0, 'skip_turn': False,
                    'expired': [], 'active': []}

        total_damage = 0
        atk_penalty  = 0
        skip_turn    = False
        expired      = []
        still_active = []

        for eff in buffs:
            key  = eff.get('key', '')
            defn = STATUS_EFFECTS.get(key, {})
            if not defn:
                continue

            dmg_notation = defn.get('damage_per_turn')
            if dmg_notation:
                total_damage += self.dice.roll(dmg_notation)[2]
            if defn.get('skip_turn'):
                skip_turn = True
            atk_penalty += defn.get('attack_penalty', 0)

            eff = dict(eff)
            eff['turns_remaining'] = eff.get('turns_remaining', 1) - 1
            if eff['turns_remaining'] <= 0:
                expired.append(key)
            else:
                still_active.append(eff)

        known['_player_buffs'] = still_active
        current_state.known_entities = known

        return {
            'damage':      total_damage,
            'atk_penalty': atk_penalty,
            'skip_turn':   skip_turn,
            'expired':     expired,
            'active':      [e['key'] for e in still_active],
        }

    def get_player_active_statuses(self, current_state):
        """Return list of active status effect keys on the player."""
        known = current_state.known_entities or {}
        return [e['key'] for e in known.get('_player_buffs', [])
                if e.get('turns_remaining', 0) > 0]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_class_ability(self, character, ability_key):
        """Return the ability definition dict for this character's class, or None."""
        if not ability_key:
            return None
        char_class = (character.char_class or 'warrior').lower().strip()
        class_def  = CLASS_ABILITIES.get(char_class, {})
        return class_def.get(ability_key)

    def _get_passive_reduction(self, target_entry):
        """Return flat damage reduction from passive specials (e.g. stone_skin)."""
        from data.monsters import SPECIAL_ABILITIES
        special_key = target_entry.get('special_ability', '')
        defn = SPECIAL_ABILITIES.get(special_key or '', {})
        if defn.get('effect') == 'damage_reduction':
            return defn.get('value', 0)
        return 0


# ---------------------------------------------------------------------------
# Public helper: detect class ability from raw player text
# ---------------------------------------------------------------------------

import re

# Build a flat lookup: pattern → (char_class, ability_key)
_ABILITY_PATTERNS = []
for _cls, _abilities in CLASS_ABILITIES.items():
    for _akey, _adef in _abilities.items():
        for _kw in _adef.get('keywords_en', []) + _adef.get('keywords_cn', []):
            _ABILITY_PATTERNS.append((re.compile(re.escape(_kw), re.I), _cls, _akey))


def detect_class_ability(player_action, char_class):
    """
    Return ability_key if the player's action contains a class-ability keyword
    matching their class, else None.
    """
    char_class = (char_class or '').lower().strip()
    for pattern, cls, akey in _ABILITY_PATTERNS:
        if cls == char_class and pattern.search(player_action):
            return akey
    return None


def get_ability_definition(char_class, ability_key):
    """Return the CLASS_ABILITIES entry for a given class + ability key."""
    return CLASS_ABILITIES.get((char_class or '').lower(), {}).get(ability_key)

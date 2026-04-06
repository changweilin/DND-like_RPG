# data/monsters.py
# DND-inspired monster & enemy NPC roster.
#
# Organised into 4 tiers matching the 3-tier learning curve + boss tier:
#   Tier 1 — 初學 (Novice)          CR 1/4–1   Starter encounters
#   Tier 2 — 冒險者 (Adventurer)    CR 2–4     Mid-game encounters
#   Tier 3 — 後期 (Late-game)       CR 5–8     Challenging encounters
#   Tier 4 — 精英首領 (Elite Boss)  CR 9+      Boss encounters; is_boss: True
#
# Base stats are calibrated for "normal" difficulty.
# get_entity_base_stats() in intent_parser.py applies ±20% variance
# and scales for other difficulty levels automatically.
#
# damage_dice  — the creature's weapon/natural attack notation.
# special_ability — key into SPECIAL_ABILITIES (drives rule-engine effects).
# resistances / weaknesses — damage-type tags used by the combat engine.
# undead / construct — boolean flags for Turn Undead and other class features.

import re

# ---------------------------------------------------------------------------
# Special ability definitions
# ---------------------------------------------------------------------------

SPECIAL_ABILITIES = {
    # --- Offensive / on-hit ---
    'pack_tactics': {
        'name': 'Pack Tactics',
        'cn_name': '群體戰術',
        'trigger': 'on_attack',
        'effect': 'hit_bonus',
        'value': 2,
        'condition': 'ally_adjacent',
        'description': 'Gains +2 to attack rolls while an ally is nearby.',
    },
    'martial_advantage': {
        'name': 'Martial Advantage',
        'cn_name': '戰術優勢',
        'trigger': 'on_hit',
        'effect': 'bonus_damage_dice',
        'value': '2d6',
        'condition': 'ally_adjacent',
        'description': 'Deals bonus damage when an ally flanks the target.',
    },
    'paralyzing_touch': {
        'name': 'Paralyzing Touch',
        'cn_name': '癱瘓之觸',
        'trigger': 'on_hit',
        'effect': 'apply_status',
        'status': 'stunned',
        'dc': 13,
        'description': 'Hit target must succeed DC 13 CON or be stunned 1 turn.',
    },
    'life_drain': {
        'name': 'Life Drain',
        'cn_name': '生命汲取',
        'trigger': 'on_hit',
        'effect': 'lifesteal',
        'value': '1d6',
        'description': 'Drains HP from target and heals itself.',
    },
    'disease_bite': {
        'name': 'Disease Bite',
        'cn_name': '疾病噬咬',
        'trigger': 'on_hit',
        'effect': 'apply_status',
        'status': 'poisoned',
        'dc': 11,
        'description': 'Bite may infect target with a festering disease.',
    },
    'rampage': {
        'name': 'Rampage',
        'cn_name': '狂暴衝擊',
        'trigger': 'on_kill',
        'effect': 'bonus_attack',
        'description': 'Gains a bonus attack when it kills a creature.',
    },
    'venomous_sting': {
        'name': 'Venomous Sting',
        'cn_name': '劇毒刺擊',
        'trigger': 'on_hit',
        'effect': 'apply_status',
        'status': 'poisoned',
        'dc': 14,
        'description': 'Sting injects deadly venom.',
    },
    'tail_spike': {
        'name': 'Tail Spike',
        'cn_name': '尾刺投擲',
        'trigger': 'on_attack',
        'effect': 'ranged_attack',
        'damage_dice': '1d8',
        'description': 'Launches iron spikes from its tail — ignores melee range.',
    },
    'breath_weapon_fire': {
        'name': 'Fire Breath',
        'cn_name': '火焰吐息',
        'trigger': 'on_attack',
        'effect': 'aoe_damage',
        'damage_dice': '4d8',
        'status': 'burning',
        'dc': 17,
        'description': 'Breathes a cone of fire dealing AoE damage; DC 17 DEX or burning.',
    },
    'breath_weapon_cold': {
        'name': 'Cold Breath',
        'cn_name': '冰霜吐息',
        'trigger': 'on_attack',
        'effect': 'aoe_damage',
        'damage_dice': '3d8',
        'status': 'slowed',
        'dc': 15,
        'description': 'Breathes a blast of frost; DC 15 CON or slowed.',
    },
    'multiattack_2': {
        'name': 'Multiattack',
        'cn_name': '多重攻擊',
        'trigger': 'on_attack',
        'effect': 'extra_attacks',
        'extra': 1,
        'description': 'Attacks twice per round.',
    },
    'multiattack_3': {
        'name': 'Multiattack III',
        'cn_name': '三連攻擊',
        'trigger': 'on_attack',
        'effect': 'extra_attacks',
        'extra': 2,
        'description': 'Attacks three times per round.',
    },
    # --- Defensive / passive ---
    'undead_fortitude': {
        'name': 'Undead Fortitude',
        'cn_name': '不死韌性',
        'trigger': 'on_lethal_hit',
        'effect': 'survive_once',
        'dc': 10,
        'description': 'When reduced to 0 HP, DC 10 CON save to survive with 1 HP instead (once).',
    },
    'regeneration': {
        'name': 'Regeneration',
        'cn_name': '再生',
        'trigger': 'turn_start',
        'effect': 'heal_per_turn',
        'value': 10,
        'suppress_on': ['burning'],
        'description': 'Regains 10 HP at the start of each turn unless on fire.',
    },
    'stone_skin': {
        'name': 'Stone Skin',
        'cn_name': '石膚',
        'trigger': 'passive',
        'effect': 'damage_reduction',
        'value': 3,
        'description': 'Natural armour reduces all physical damage by 3.',
    },
    'death_saves': {
        'name': 'Legendary Resistance',
        'cn_name': '傳奇抵抗',
        'trigger': 'on_status',
        'effect': 'negate_status',
        'uses': 3,
        'description': 'Can choose to succeed on a saving throw 3 times per encounter.',
    },
    'spellcasting': {
        'name': 'Spellcasting',
        'cn_name': '施法',
        'trigger': 'on_attack',
        'effect': 'magic_attack',
        'damage_dice': '2d8',
        'description': 'Unleashes a magical blast instead of a physical strike.',
    },
    'strength_drain': {
        'name': 'Strength Drain',
        'cn_name': '力量汲取',
        'trigger': 'on_hit',
        'effect': 'stat_penalty',
        'stat': 'atk',
        'value': -2,
        'description': 'Reduces target ATK by 2 on a hit.',
    },
    'trip_attack': {
        'name': 'Trip',
        'cn_name': '絆倒',
        'trigger': 'on_critical',
        'effect': 'apply_status',
        'status': 'stunned',
        'description': 'Knocks the target prone on a critical hit.',
    },
    'luring_song': {
        'name': 'Luring Song',
        'cn_name': '誘惑歌聲',
        'trigger': 'on_attack',
        'effect': 'charm_attempt',
        'dc': 13,
        'description': 'Attempts to charm the player; DC 13 WIS or skip next action.',
    },
    'berserk': {
        'name': 'Berserk',
        'cn_name': '狂暴化',
        'trigger': 'on_damage_taken',
        'effect': 'atk_bonus',
        'value': 3,
        'threshold_hp': 0.5,
        'description': 'Gains +3 ATK when below half HP.',
    },
    'infernal_rage': {
        'name': 'Infernal Rage',
        'cn_name': '地獄之怒',
        'trigger': 'on_attack',
        'effect': 'extra_attacks',
        'extra': 1,
        'bonus_damage': '1d6',
        'description': 'Extra attack with +1d6 fire damage.',
    },
    'cursed_bite': {
        'name': 'Cursed Bite',
        'cn_name': '詛咒噬咬',
        'trigger': 'on_hit',
        'effect': 'apply_status',
        'status': 'bleeding',
        'dc': 15,
        'description': 'Bite inflicts a cursed wound that bleeds persistently.',
    },
    'charge': {
        'name': 'Charge',
        'cn_name': '衝鋒',
        'trigger': 'on_attack',
        'effect': 'bonus_damage_dice',
        'value': '1d6',
        'condition': 'first_attack',
        'description': 'Deals bonus damage on the first attack of combat.',
    },
}

# ---------------------------------------------------------------------------
# Monster roster
# ---------------------------------------------------------------------------
# Stats listed are base values for "normal" difficulty; intent_parser's
# get_entity_base_stats() applies ±20 % variance per encounter.
# difficulty_scale: multiplier applied to hp/atk/def per difficulty step.

MONSTER_ROSTER = {

    # ===================================================================
    # TIER 1 — Easy  (CR 1/4–1)
    # ===================================================================

    'goblin': {
        'display_name': 'Goblin', 'cn_name': '哥布林',
        'tier': 1, 'type': 'monster',
        'hp': 7, 'atk': 6, 'def_stat': 5,
        'damage_dice': '1d6',
        'special_ability': 'pack_tactics',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Gold Coins', 'Rusty Dagger', 'Goblin Ears'],
        'description': 'A wiry green-skinned creature with beady yellow eyes and a manic grin.',
        'xp': 25,
    },
    'kobold': {
        'display_name': 'Kobold', 'cn_name': '柯博德',
        'tier': 1, 'type': 'monster',
        'hp': 5, 'atk': 5, 'def_stat': 4,
        'damage_dice': '1d4',
        'special_ability': 'pack_tactics',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Copper Coins', 'Crude Spear', 'Kobold Scale'],
        'description': 'A small draconic humanoid that attacks in swarms.',
        'xp': 25,
    },
    'giant_rat': {
        'display_name': 'Giant Rat', 'cn_name': '巨鼠',
        'tier': 1, 'type': 'monster',
        'hp': 7, 'atk': 5, 'def_stat': 4,
        'damage_dice': '1d4',
        'special_ability': 'disease_bite',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Rat Pelt', 'Vermin Tooth'],
        'description': 'A dog-sized rodent with matted grey fur and festering yellow teeth.',
        'xp': 25,
    },
    'skeleton': {
        'display_name': 'Skeleton', 'cn_name': '骷髏',
        'tier': 1, 'type': 'monster',
        'hp': 13, 'atk': 8, 'def_stat': 6,
        'damage_dice': '1d6',
        'special_ability': None,
        'resistances': ['pierce'],
        'weaknesses': ['bludgeon'],
        'undead': True, 'construct': False,
        'loot': ['Bone Fragment', 'Rusty Sword', 'Tattered Cloth'],
        'description': 'An animated heap of bones held together by dark necromantic energy.',
        'xp': 50,
    },
    'zombie': {
        'display_name': 'Zombie', 'cn_name': '殭屍',
        'tier': 1, 'type': 'monster',
        'hp': 22, 'atk': 6, 'def_stat': 4,
        'damage_dice': '1d6',
        'special_ability': 'undead_fortitude',
        'resistances': [], 'weaknesses': ['fire', 'radiant'],
        'undead': True, 'construct': False,
        'loot': ['Rotting Cloth', 'Old Coin Pouch'],
        'description': 'A shambling rotting corpse, eyes milky white, driven by mindless hunger.',
        'xp': 50,
    },
    'wolf': {
        'display_name': 'Wolf', 'cn_name': '狼',
        'tier': 1, 'type': 'monster',
        'hp': 11, 'atk': 9, 'def_stat': 5,
        'damage_dice': '1d6',
        'special_ability': 'trip_attack',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Wolf Pelt', 'Sharp Fang'],
        'description': 'A sleek grey predator with amber eyes and razor fangs.',
        'xp': 50,
    },
    'bandit': {
        'display_name': 'Bandit', 'cn_name': '盜匪',
        'tier': 1, 'type': 'guard',
        'hp': 11, 'atk': 8, 'def_stat': 7,
        'damage_dice': '1d6',
        'special_ability': None,
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Silver Coins', 'Short Sword', 'Leather Vest'],
        'description': 'A desperate outlaw, scarred and weather-beaten, eyes darting for opportunity.',
        'xp': 25,
    },
    'cultist': {
        'display_name': 'Cultist', 'cn_name': '邪教信徒',
        'tier': 1, 'type': 'monster',
        'hp': 9, 'atk': 7, 'def_stat': 5,
        'damage_dice': '1d6',
        'special_ability': 'spellcasting',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Dark Robe', 'Ritual Dagger', 'Cult Medallion'],
        'description': 'A zealot marked with profane symbols, chanting in a dead tongue.',
        'xp': 50,
    },

    # ===================================================================
    # TIER 2 — Normal  (CR 2–4)
    # ===================================================================

    'orc': {
        'display_name': 'Orc', 'cn_name': '獸人',
        'tier': 2, 'type': 'monster',
        'hp': 15, 'atk': 12, 'def_stat': 9,
        'damage_dice': '1d8',
        'special_ability': 'charge',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Iron Axe', 'Orcish Tusks', 'Crude Shield'],
        'description': 'A hulking grey-skinned brute with tusked jaws and murderous eyes.',
        'xp': 100,
    },
    'hobgoblin': {
        'display_name': 'Hobgoblin', 'cn_name': '地精戰士',
        'tier': 2, 'type': 'guard',
        'hp': 11, 'atk': 11, 'def_stat': 12,
        'damage_dice': '1d8',
        'special_ability': 'martial_advantage',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Chain Mail Fragment', 'Military Sword', 'Hobgoblin Standard'],
        'description': 'A disciplined red-skinned soldier in battered armour, moving with military precision.',
        'xp': 100,
    },
    'gnoll': {
        'display_name': 'Gnoll', 'cn_name': '土狼人',
        'tier': 2, 'type': 'monster',
        'hp': 22, 'atk': 10, 'def_stat': 8,
        'damage_dice': '2d4',
        'special_ability': 'rampage',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Gnoll Skull', 'Crude Longbow', 'Spotted Hide'],
        'description': 'A hyena-headed humanoid reeking of carrion, laughing as it kills.',
        'xp': 100,
    },
    'ghoul': {
        'display_name': 'Ghoul', 'cn_name': '食屍鬼',
        'tier': 2, 'type': 'monster',
        'hp': 22, 'atk': 11, 'def_stat': 6,
        'damage_dice': '2d4',
        'special_ability': 'paralyzing_touch',
        'resistances': [], 'weaknesses': ['radiant'],
        'undead': True, 'construct': False,
        'loot': ['Ghoul Claw', 'Desecrated Token', 'Grave Soil'],
        'description': 'A lithe undead predator with elongated claws and a ravenous hunger.',
        'xp': 200,
    },
    'shadow': {
        'display_name': 'Shadow', 'cn_name': '暗影',
        'tier': 2, 'type': 'monster',
        'hp': 16, 'atk': 10, 'def_stat': 7,
        'damage_dice': '1d6',
        'special_ability': 'strength_drain',
        'resistances': ['pierce', 'slash'], 'weaknesses': ['radiant'],
        'undead': True, 'construct': False,
        'loot': ['Shadow Essence', 'Void Shard'],
        'description': 'A living patch of darkness that whispers of oblivion.',
        'xp': 100,
    },
    # ogre: added to fill the Tier 2 mid-range slot vacated by wight/werewolf
    'ogre': {
        'display_name': 'Ogre', 'cn_name': '食人魔',
        'tier': 2, 'type': 'monster',
        'hp': 35, 'atk': 13, 'def_stat': 9,
        'damage_dice': '2d6',
        'special_ability': 'rampage',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Ogre Club', 'Hide Sack', 'Stolen Gold'],
        'description': 'A brutish hulk twice the size of a man, driven by hunger and rage.',
        'xp': 300,
    },
    'wight': {
        'display_name': 'Wight', 'cn_name': '幽靈騎士',
        'tier': 3, 'type': 'monster',   # promoted: HP 45 + life_drain exceeds Tier 2 range
        'hp': 45, 'atk': 12, 'def_stat': 10,
        'damage_dice': '1d8',
        'special_ability': 'life_drain',
        'resistances': ['poison', 'cold'], 'weaknesses': ['radiant'],
        'undead': True, 'construct': False,
        'loot': ['Wight Soul Gem', 'Corroded Armour Piece', 'Ancient Coin'],
        'description': 'A fallen warrior encased in tarnished armour, its eyes like cold embers.',
        'xp': 1800,
    },
    'harpy': {
        'display_name': 'Harpy', 'cn_name': '鷹身女妖',
        'tier': 2, 'type': 'monster',
        'hp': 38, 'atk': 10, 'def_stat': 7,
        'damage_dice': '1d6',
        'special_ability': 'luring_song',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Harpy Feather', 'Talon Fragment', 'Shiny Trinket'],
        'description': 'A winged woman-beast with a voice like silver and eyes like venom.',
        'xp': 200,
    },
    'werewolf': {
        'display_name': 'Werewolf', 'cn_name': '狼人',
        'tier': 3, 'type': 'monster',   # promoted: HP 58 + silver immunity exceeds Tier 2 range
        'hp': 58, 'atk': 12, 'def_stat': 11,
        'damage_dice': '2d4',
        'special_ability': 'cursed_bite',
        'resistances': ['slash_nonsilver', 'pierce_nonsilver'], 'weaknesses': ['silver'],
        'undead': False, 'construct': False,
        'loot': ['Silver-Tipped Arrow', 'Werewolf Pelt', 'Moon Stone'],
        'description': 'A towering half-man half-wolf that hunts by moonlight.',
        'xp': 1800,
    },

    # ===================================================================
    # TIER 3 — Hard  (CR 5–8)
    # ===================================================================

    'troll': {
        'display_name': 'Troll', 'cn_name': '巨魔',
        'tier': 3, 'type': 'monster',
        'hp': 84, 'atk': 14, 'def_stat': 11,
        'damage_dice': '2d6',
        'special_ability': 'regeneration',
        'resistances': [], 'weaknesses': ['fire', 'acid'],
        'undead': False, 'construct': False,
        'loot': ['Troll Hide', 'Regeneration Gland', 'Mossy Club'],
        'description': 'A lanky green giant with rubbery skin that knits together before your eyes.',
        'xp': 1800,
    },
    'vampire_spawn': {
        'display_name': 'Vampire Spawn', 'cn_name': '吸血鬼眷屬',
        'tier': 3, 'type': 'monster',
        'hp': 82, 'atk': 14, 'def_stat': 11,
        'damage_dice': '2d6',
        'special_ability': 'life_drain',
        'resistances': ['poison'], 'weaknesses': ['radiant', 'fire'],
        'undead': True, 'construct': False,
        'loot': ['Vampire Fang', 'Blood Vial', 'Silk Cloak'],
        'description': 'A pale, red-eyed humanoid bound to an elder vampire, elegant but deadly.',
        'xp': 1800,
    },
    'manticore': {
        'display_name': 'Manticore', 'cn_name': '蠍尾獅',
        'tier': 3, 'type': 'monster',
        'hp': 68, 'atk': 14, 'def_stat': 10,
        'damage_dice': '1d8',
        'special_ability': 'tail_spike',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Manticore Spine', 'Lion Mane', 'Venomous Tail Barb'],
        'description': 'A lion-bodied beast with a human face and a tail bristling with iron spikes.',
        'xp': 1800,
    },
    'flesh_golem': {
        'display_name': 'Flesh Golem', 'cn_name': '血肉魔像',
        'tier': 3, 'type': 'monster',
        'hp': 93, 'atk': 13, 'def_stat': 11,
        'damage_dice': '2d8',
        'special_ability': 'berserk',
        'resistances': ['poison', 'slash'], 'weaknesses': ['lightning', 'fire'],
        'undead': False, 'construct': True,
        'loot': ['Stitched Flesh Slab', 'Alchemical Bolt', 'Rusted Bolt'],
        'description': 'Stitched from the corpses of the slain, animated by lightning and mad science.',
        'xp': 1800,
    },
    'enemy_mage': {
        'display_name': 'Dark Mage', 'cn_name': '黑魔法師',
        'tier': 3, 'type': 'monster',
        'hp': 40, 'atk': 13, 'def_stat': 9,
        'damage_dice': '2d6',
        'special_ability': 'spellcasting',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Spell Tome Fragment', 'Arcane Focus', 'Robe of Shadows'],
        'description': 'A robed figure whose fingers crackle with barely contained power.',
        'xp': 1800,
    },
    'basilisk': {
        'display_name': 'Basilisk', 'cn_name': '石化蜥蜴',
        'tier': 3, 'type': 'monster',
        'hp': 52, 'atk': 13, 'def_stat': 10,
        'damage_dice': '2d6',
        'special_ability': 'venomous_sting',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Basilisk Eye', 'Stone-touched Scale', 'Venom Gland'],
        'description': 'An eight-legged lizard whose gaze can turn flesh to stone.',
        'xp': 1800,
    },

    # ===================================================================
    # TIER 4 — Deadly / Boss  (CR 9+)
    # ===================================================================

    'stone_giant': {
        'display_name': 'Stone Giant', 'cn_name': '石巨人',
        'tier': 4, 'type': 'boss', 'is_boss': True,
        'hp': 126, 'atk': 17, 'def_stat': 14,
        'damage_dice': '3d8',
        'special_ability': 'stone_skin',
        'resistances': ['bludgeon'], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Giant Heart Stone', 'Megalith Club', 'Stone-carved Rune'],
        'description': 'A mountain-born colossus whose skin is living granite.',
        'xp': 5000,
    },
    'vampire_lord': {
        'display_name': 'Vampire Lord', 'cn_name': '吸血鬼領主',
        'tier': 4, 'type': 'boss', 'is_boss': True,
        'hp': 144, 'atk': 17, 'def_stat': 16,
        'damage_dice': '2d8',
        'special_ability': 'life_drain',
        'resistances': ['poison', 'cold'], 'weaknesses': ['radiant', 'fire'],
        'undead': True, 'construct': False,
        'loot': ['Vampire Lord Ring', 'Crimson Cape', 'Ancient Blood Vial', 'Castle Key'],
        'description': 'An immortal aristocrat whose very presence drains the will to fight.',
        'xp': 10000,
    },
    'lich': {
        'display_name': 'Lich', 'cn_name': '巫妖',
        'tier': 4, 'type': 'boss', 'is_boss': True,
        'hp': 135, 'atk': 18, 'def_stat': 17,
        'damage_dice': '3d6',
        'special_ability': 'spellcasting',
        'resistances': ['cold', 'lightning', 'poison'], 'weaknesses': ['radiant'],
        'undead': True, 'construct': False,
        'loot': ['Phylactery Shard', 'Lich Crown', 'Necrotic Tome', 'Death Amulet'],
        'description': 'An archmage who traded mortality for power, now a hollow skull-faced horror.',
        'xp': 10000,
    },
    'demon_lord': {
        'display_name': 'Demon Lord', 'cn_name': '惡魔領主',
        'tier': 4, 'type': 'boss', 'is_boss': True,
        'hp': 200, 'atk': 20, 'def_stat': 18,
        'damage_dice': '2d10',
        'special_ability': 'infernal_rage',
        'resistances': ['fire', 'poison', 'cold'], 'weaknesses': ['radiant'],
        'undead': False, 'construct': False,
        'loot': ['Demon Core', 'Hellfire Sword', 'Abyssal Sigil', 'Infernal Contract'],
        'description': 'A towering creature of pure malice and infernal flame.',
        'xp': 25000,
    },
    'ancient_dragon': {
        'display_name': 'Ancient Dragon', 'cn_name': '遠古巨龍',
        'tier': 4, 'type': 'boss', 'is_boss': True,
        'hp': 367, 'atk': 20, 'def_stat': 19,
        'damage_dice': '2d10',
        'special_ability': 'breath_weapon_fire',
        'resistances': ['fire'], 'weaknesses': ['cold'],
        'undead': False, 'construct': False,
        'loot': ['Dragon Scale', 'Dragon Heart', 'Ancient Hoard Coin', 'Dragon Eye Gem'],
        'description': 'A crimson titan whose shadow blots the sun and whose breath melts iron.',
        'xp': 25000,
    },
    'frost_dragon': {
        'display_name': 'Frost Dragon', 'cn_name': '冰霜龍',
        'tier': 4, 'type': 'boss', 'is_boss': True,
        'hp': 300, 'atk': 19, 'def_stat': 18,
        'damage_dice': '2d10',
        'special_ability': 'breath_weapon_cold',
        'resistances': ['cold'], 'weaknesses': ['fire'],
        'undead': False, 'construct': False,
        'loot': ['Frost Dragon Scale', 'Glacial Shard', 'Ice Heart Gem'],
        'description': 'A pale serpentine leviathan whose breath flash-freezes all before it.',
        'xp': 25000,
    },
    'orc_warchief': {
        'display_name': 'Orc Warchief', 'cn_name': '獸人首領',
        'tier': 4, 'type': 'boss', 'is_boss': True,
        'hp': 93, 'atk': 16, 'def_stat': 14,
        'damage_dice': '2d8',
        'special_ability': 'multiattack_2',
        'resistances': [], 'weaknesses': [],
        'undead': False, 'construct': False,
        'loot': ['Warchief Waraxe', 'Iron War Crown', 'Warchief Cloak'],
        'description': 'A scarred giant among orcs, who has clawed his way to power through pure savagery.',
        'xp': 5000,
    },
}

# ---------------------------------------------------------------------------
# Boss story hooks — deterministic consequences triggered when a Tier 4 boss
# is defeated.  Each entry drives _apply_boss_story_hooks() in events.py.
#
# guaranteed_loot  — item names always added to inventory (skip 50 % roll)
# narrative_hint   — injected into LLM outcome_context as STORY CONSEQUENCE
# npc_changes      — {npc_display_name: affinity_delta} applied immediately
# quest_name       — auto-complete the matching active quest (if it exists)
# location_unlock  — string stored as _unlocked_<boss_key> in known_entities
# ---------------------------------------------------------------------------

BOSS_STORY_HOOKS = {
    'orc_warchief': {
        'guaranteed_loot': ['Warchief Waraxe'],
        'narrative_hint': (
            "The Orc Warchief falls. Without their warlord the orc warband "
            "scatters in disarray. Nearby villages are now safe from raids. "
            "Villagers and guards who lived in fear of orc attacks will be grateful."
        ),
        'npc_changes': {'Village Elder': 15, 'Town Guard Captain': 10},
        'quest_name': 'Defeat the Orc Warchief',
    },
    'vampire_lord': {
        'guaranteed_loot': ['Castle Key'],
        'narrative_hint': (
            "The Vampire Lord crumbles to ash. The dark curse shrouding the castle "
            "begins to lift. The Castle Key in your hands now opens the sealed "
            "inner sanctum. Surviving thralls and spawn flee into the night."
        ),
        'npc_changes': {'Castle Steward': 20},
        'quest_name': 'Break the Vampire Curse',
        'location_unlock': 'Vampire Lord Inner Sanctum',
    },
    'lich': {
        'guaranteed_loot': ['Phylactery Shard'],
        'narrative_hint': (
            "The Lich's physical form is destroyed. Its phylactery cracks with a "
            "sound like breaking worlds. The undead legions it commanded collapse "
            "into inanimate bone. Ancient scholars will seek the Phylactery Shard."
        ),
        'npc_changes': {'Archmage Advisor': 25, 'Temple High Priest': 20},
        'quest_name': 'Shatter the Lich Phylactery',
        'location_unlock': 'Lich Tomb Lower Chambers',
    },
    'demon_lord': {
        'guaranteed_loot': ['Infernal Contract'],
        'narrative_hint': (
            "The Demon Lord is banished back to the Abyss. The infernal rift seals "
            "shut. The Infernal Contract it held — binding countless souls — is now "
            "in your hands. Demonic cultists lose their patron and scatter in terror."
        ),
        'npc_changes': {'Arch-Priest': 30, 'Royal Emissary': 20},
        'quest_name': 'Seal the Infernal Rift',
        'location_unlock': 'Sealed Demonic Sanctum',
    },
    'ancient_dragon': {
        'guaranteed_loot': ['Dragon Eye Gem'],
        'narrative_hint': (
            "The Ancient Dragon falls at last. A legendary deed — bards will sing "
            "of this day for generations. The Dragon Eye Gem pulses with residual "
            "draconic power. The vast hoard lies unguarded deep in the lair."
        ),
        'npc_changes': {'King': 35, 'Guild Master': 25},
        'quest_name': 'Slay the Ancient Dragon',
        'location_unlock': "Ancient Dragon's Hoard Vault",
    },
    'frost_dragon': {
        'guaranteed_loot': ['Ice Heart Gem'],
        'narrative_hint': (
            "The Frost Dragon's corpse steams in the warming air. The perpetual "
            "blizzard that blanketed the northern lands begins to fade. "
            "The Ice Heart Gem contains the crystallised essence of its power."
        ),
        'npc_changes': {'Northern Tribe Elder': 25, 'Ice Fisher': 15},
        'quest_name': 'End the Eternal Winter',
        'location_unlock': 'Frost Dragon Ice Cavern',
    },
    'stone_giant': {
        'guaranteed_loot': ['Giant Heart Stone'],
        'narrative_hint': (
            "The Stone Giant crashes to the ground. The mountain passes it "
            "controlled are now open. Caravans and refugees can move freely again. "
            "The Giant Heart Stone radiates immense earth-shaping power."
        ),
        'npc_changes': {'Merchant Guild Leader': 20, 'Mountain Guide': 15},
        'quest_name': 'Clear the Mountain Pass',
        'location_unlock': 'Eastern Mountain Pass',
    },
}

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

# Build a flat list of all name variants → roster key for fast lookup
_ALIAS_MAP = {}
for _key, _entry in MONSTER_ROSTER.items():
    _ALIAS_MAP[_key] = _key
    _ALIAS_MAP[_entry['display_name'].lower()] = _key
    _ALIAS_MAP[_entry['cn_name']] = _key
    # Also map individual words longer than 3 chars
    for _word in re.split(r'[\s_]', _key):
        if len(_word) > 3:
            _ALIAS_MAP[_word] = _key


def get_monster_by_name(entity_name):
    """
    Return the monster roster entry matching entity_name, or None.
    Tries exact key, then alias map, then substring scan.
    """
    name_low = entity_name.lower().strip()
    if name_low in MONSTER_ROSTER:
        return MONSTER_ROSTER[name_low]
    if name_low in _ALIAS_MAP:
        return MONSTER_ROSTER[_ALIAS_MAP[name_low]]
    # Substring scan — prefer longer match
    candidates = [
        (k, v) for k, v in MONSTER_ROSTER.items()
        if k in name_low
        or v['display_name'].lower() in name_low
        or v['cn_name'] in name_low
    ]
    if candidates:
        candidates.sort(key=lambda x: len(x[0]), reverse=True)
        return candidates[0][1]
    return None


def get_special_ability(ability_key):
    """Return the special ability definition dict, or None."""
    if ability_key is None:
        return None
    return SPECIAL_ABILITIES.get(ability_key)

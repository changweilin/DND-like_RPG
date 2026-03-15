import random
import re

class DiceRoller:
    """
    Deterministic dice roller for TRPG mechanics.

    The LLM must NEVER simulate dice rolls — it tends toward narratively
    convenient results (sycophancy). This class is the authoritative,
    unbiased source of randomness in the game.

    Supports standard TRPG notation: "1d20", "2d6+3", "3d8-1", "1d4", etc.
    """

    _NOTATION = re.compile(r'^(\d+)d(\d+)([+-]\d+)?$', re.IGNORECASE)

    def roll(self, notation):
        """
        Parse and roll standard dice notation.
        Returns (individual_rolls: list[int], modifier: int, total: int).
        Raises ValueError for unrecognised notation.
        """
        notation = notation.strip().replace(' ', '')
        m = self._NOTATION.match(notation)
        if not m:
            raise ValueError(f"Invalid dice notation: {notation!r}")

        num_dice = int(m.group(1))
        die_size = int(m.group(2))
        modifier = int(m.group(3)) if m.group(3) else 0

        rolls = [random.randint(1, die_size) for _ in range(num_dice)]
        total = sum(rolls) + modifier
        return rolls, modifier, total

    def roll_skill_check(self, dc, modifier=0):
        """
        Roll 1d20 + modifier against a Difficulty Class (DC).

        Outcomes:
          'critical_success' — natural 20 (always succeeds regardless of DC)
          'success'          — total >= DC
          'failure'          — total < DC
          'critical_failure' — natural 1 (always fails regardless of modifier)

        Returns a dict with full roll details for the rule engine and
        narrative renderer.
        """
        rolls, _, _ = self.roll('1d20')
        raw = rolls[0]
        total = raw + modifier

        if raw == 20:
            outcome = 'critical_success'
        elif raw == 1:
            outcome = 'critical_failure'
        elif total >= dc:
            outcome = 'success'
        else:
            outcome = 'failure'

        notation = f'1d20+{modifier}' if modifier >= 0 else f'1d20{modifier}'
        return {
            'raw_roll': raw,
            'modifier': modifier,
            'total': total,
            'dc': dc,
            'outcome': outcome,
            'notation': notation,
        }

    def roll_damage(self, notation):
        """
        Roll damage dice (e.g. '2d6', '1d8+3').
        Returns total damage (minimum 0 — modifiers cannot produce negative damage).
        """
        _, _, total = self.roll(notation)
        return max(0, total)

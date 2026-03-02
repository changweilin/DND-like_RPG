import time

class EventManager:
    def __init__(self, llm_client, rag_system, db_session):
        self.llm = llm_client
        self.rag = rag_system
        self.session = db_session
        
    def process_turn(self, player_action, current_state, character):
        """Main game loop turn processing."""
        
        # 1. Retrieve Context
        context = self.rag.retrieve_context(player_action)
        
        # 2. System Prompt Formulation
        system_prompt = f"""
        You are a strict, creative Dungeon Master for a text RPG.
        The player is {character.name}, a {character.race} {character.char_class}.
        HP: {character.hp}/{character.max_hp}, MP: {character.mp}/{character.max_mp}.
        Current Location: {current_state.current_location}.
        World Context: {current_state.world_context}.
        
        CRITICAL: You MUST write the 'narrative' and 'choices' exclusively in this language: {current_state.language or "English"}.
        Describe what happens next based on their action. Be engaging and atmospheric.
        """
        
        # 3. Generate narrative and choices via single JSON structured call
        turn_data = self.llm.generate_turn(system_prompt, player_action, context)
        
        narrative = turn_data.get('narrative', "The DM stares blankly into space...")
        choices = turn_data.get('choices', ["Look around", "Wait"])
        
        # Apply Mechanics
        if turn_data.get('damage_taken') or turn_data.get('hp_healed') or turn_data.get('items_found'):
            from engine.character import CharacterLogic
            logic = CharacterLogic(self.session, character)

            if turn_data.get('damage_taken'):
                logic.take_damage(turn_data['damage_taken'])

            if turn_data.get('hp_healed'):
                logic.heal(turn_data['hp_healed'])

            if turn_data.get('items_found'):
                for item in turn_data['items_found']:
                    logic.add_item({'name': item})

        # 5. Save to Context / Memory
        event_id = f"event_{character.id}_{current_state.id}_{int(time.time() * 1000)}"
        self.rag.add_story_event(f"Player chose: {player_action}\nResult: {narrative}", event_id=event_id)
        
        return narrative, choices, turn_data

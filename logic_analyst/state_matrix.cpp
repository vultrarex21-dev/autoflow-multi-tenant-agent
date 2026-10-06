#include "state_matrix.h"

std::string state_to_string(State state) {
    switch (state) {
        case State::New: return "New";
        case State::Contacted: return "Contacted";
        case State::Cooldown: return "Cooldown";
        case State::Replied: return "Replied";
        case State::Converted: return "Converted";
        case State::DoNotContact: return "DoNotContact";
        case State::DataError: return "DataError";
        default: return "Unknown";
    }
}

StateMatrix::StateMatrix() {
    // Initialize adjacency matrix representing valid deterministic transitions
    // Row: Current State, Column: Target State
    for (int i = 0; i < 7; ++i) {
        for (int j = 0; j < 7; ++j) {
            matrix[i][j] = false;
        }
    }

    // New -> Contacted, DataError
    matrix[(int)State::New][(int)State::Contacted] = true;
    matrix[(int)State::New][(int)State::DataError] = true;

    // Contacted -> Cooldown, Replied, DoNotContact, DataError
    matrix[(int)State::Contacted][(int)State::Cooldown] = true;
    matrix[(int)State::Contacted][(int)State::Replied] = true;
    matrix[(int)State::Contacted][(int)State::DoNotContact] = true;
    matrix[(int)State::Contacted][(int)State::DataError] = true;

    // Cooldown -> Contacted, Replied, DoNotContact, DataError
    matrix[(int)State::Cooldown][(int)State::Contacted] = true;
    matrix[(int)State::Cooldown][(int)State::Replied] = true;
    matrix[(int)State::Cooldown][(int)State::DoNotContact] = true;
    matrix[(int)State::Cooldown][(int)State::DataError] = true;

    // Replied -> Converted, DoNotContact, DataError
    matrix[(int)State::Replied][(int)State::Converted] = true;
    matrix[(int)State::Replied][(int)State::DoNotContact] = true;
    matrix[(int)State::Replied][(int)State::DataError] = true;

    // Converted -> DoNotContact, DataError (Allows post-conversion opt-outs or delivery flags)
    matrix[(int)State::Converted][(int)State::DoNotContact] = true;
    matrix[(int)State::Converted][(int)State::DataError] = true;

    // DoNotContact -> DataError (Locked boundary except error flags)
    matrix[(int)State::DoNotContact][(int)State::DataError] = true;
    
    // DataError is a terminal lock state until manually resolved
}

bool StateMatrix::can_transition(State from, State to) const {
    // O(1) performance check
    if (from == to) return true;
    return matrix[(int)from][(int)to];
}

std::string StateMatrix::determine_next_channel(bool has_email, bool has_linkedin, const std::string& last_channel) const {
    if (!has_email && !has_linkedin) return "none";

    if (last_channel == "none" || last_channel.empty()) {
        if (has_email) return "email";
        return "linkedin";
    }

    if (has_email && has_linkedin) {
        if (last_channel == "email") return "linkedin";
        return "email";
    }

    if (has_email) return "email";
    return "linkedin";
}

int StateMatrix::calculate_cooldown(int outreach_count) const {
    if (outreach_count == 1) return 3;
    if (outreach_count == 2) return 5;
    return 7;
}

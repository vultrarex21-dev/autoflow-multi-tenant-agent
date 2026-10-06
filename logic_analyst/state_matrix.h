#ifndef STATE_MATRIX_H
#define STATE_MATRIX_H

#include <string>

enum class State {
    New = 0,
    Contacted = 1,
    Cooldown = 2,
    Replied = 3,
    Converted = 4,
    DoNotContact = 5,
    DataError = 6
};

std::string state_to_string(State state);

class StateMatrix {
private:
    // 7x7 Adjacency Matrix representing transition limits
    bool matrix[7][7];

public:
    StateMatrix();
    
    // Validates if transition from 'from' to 'to' is allowed.
    // O(1) execution path.
    bool can_transition(State from, State to) const;

    // Evaluates next channel based on last channel and availability.
    std::string determine_next_channel(bool has_email, bool has_linkedin, const std::string& last_channel) const;

    // Calculates outreach cooldown delay in days.
    int calculate_cooldown(int outreach_count) const;
};

#endif // STATE_MATRIX_H

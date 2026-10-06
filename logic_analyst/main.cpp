#include <iostream>
#include <cassert>
#include "state_matrix.h"

void run_transition_tests(const StateMatrix& sm) {
    std::cout << "[TEST] Running High-Efficiency Transition Matrix assertions..." << std::endl;

    // Happy Path transitions
    assert(sm.can_transition(State::New, State::Contacted) == true);
    assert(sm.can_transition(State::Contacted, State::Cooldown) == true);
    assert(sm.can_transition(State::Cooldown, State::Contacted) == true);
    assert(sm.can_transition(State::Contacted, State::Replied) == true);
    assert(sm.can_transition(State::Replied, State::Converted) == true);

    // Any state to DataError
    assert(sm.can_transition(State::New, State::DataError) == true);
    assert(sm.can_transition(State::Contacted, State::DataError) == true);
    assert(sm.can_transition(State::Cooldown, State::DataError) == true);
    assert(sm.can_transition(State::Replied, State::DataError) == true);
    assert(sm.can_transition(State::DoNotContact, State::DataError) == true);

    // Prohibited transitions
    assert(sm.can_transition(State::DoNotContact, State::Contacted) == false);
    assert(sm.can_transition(State::Converted, State::Contacted) == false);
    assert(sm.can_transition(State::DataError, State::Contacted) == false);
    assert(sm.can_transition(State::New, State::Converted) == false);

    std::cout << "[PASS] Transition Matrix assertions successful." << std::endl;
}

void run_cooldown_tests(const StateMatrix& sm) {
    std::cout << "[TEST] Running mathematical Cooldown Specs..." << std::endl;
    assert(sm.calculate_cooldown(1) == 3);
    assert(sm.calculate_cooldown(2) == 5);
    assert(sm.calculate_cooldown(3) == 7);
    assert(sm.calculate_cooldown(10) == 7);
    std::cout << "[PASS] Cooldown mathematical formulas correct." << std::endl;
}

void run_omnichannel_tests(const StateMatrix& sm) {
    std::cout << "[TEST] Running Omni-channel Routing checks..." << std::endl;
    
    // Switches if both are active
    assert(sm.determine_next_channel(true, true, "none") == "email");
    assert(sm.determine_next_channel(true, true, "email") == "linkedin");
    assert(sm.determine_next_channel(true, true, "linkedin") == "email");

    // Falls back if only one active
    assert(sm.determine_next_channel(true, false, "email") == "email");
    assert(sm.determine_next_channel(false, true, "email") == "linkedin");
    assert(sm.determine_next_channel(false, false, "none") == "none");

    std::cout << "[PASS] Omni-channel Routing checks verified." << std::endl;
}

int main() {
    std::cout << "=== Autoflow Core Matrix Logic Validator (C++ Analyst) ===" << std::endl;
    
    StateMatrix sm;
    run_transition_tests(sm);
    run_cooldown_tests(sm);
    run_omnichannel_tests(sm);
    
    std::cout << "All high-efficiency O(1) logic validations passed successfully!" << std::endl;
    return 0;
}

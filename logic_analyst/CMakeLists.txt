cmake_minimum_required(VERSION 3.10)
project(AutoflowCoreLogicAnalyst CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(logic_validator
    main.cpp
    state_matrix.cpp
    state_matrix.h
)

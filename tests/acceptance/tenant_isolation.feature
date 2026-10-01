Feature: Tenant isolation and privacy
  Scenario: A manager of tenant 1 cannot see tenant 2 vehicles
    Given I am a manager of tenant 1
    When I list vehicles
    Then every vehicle belongs to tenant 1

  Scenario: Finance role receives masked locations
    Given I am a finance user of tenant 1
    When I read a vehicle state
    Then latitude and longitude have at most 2 decimal places

  Scenario: Right to erasure
    Given I am an admin of tenant 1
    When I erase a driver
    Then the driver row is masked and an ERASURE audit record exists

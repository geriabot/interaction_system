// Copyright 2026 Rodrigo Pérez-Rodríguez
//
// Licensed under the MIT License. See the LICENSE file of the repository.

#include "behaviortree_cpp/bt_factory.h"

#include "interaction_system_behaviors/AskIntelligence.hpp"
#include "interaction_system_behaviors/Listen.hpp"
#include "interaction_system_behaviors/Speak.hpp"
#include "interaction_system_behaviors/WithFillers.hpp"

BT_REGISTER_NODES(factory)
{
  factory.registerNodeType<interaction_system_behaviors::AskIntelligence>("AskIntelligence");
  factory.registerNodeType<interaction_system_behaviors::Listen>("Listen");
  factory.registerNodeType<interaction_system_behaviors::Speak>("Speak");
  factory.registerNodeType<interaction_system_behaviors::WithFillers>("WithFillers");
}

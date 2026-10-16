* This Document mainly contains information about what different configs we did wrt to the situation : Disaster Management
1) First of all, the topology chosen is ring, there are primarily three swithces, are connected to each other, this ensures that even if one switch get's disconnected or one link is not working, we have a backup route
2) There is a change in protocol and priority for different hosts keeping the situation in mind
3) Idle timeout and hard timeout are set to 0 for high priority hosts.
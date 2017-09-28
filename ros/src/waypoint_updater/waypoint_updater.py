#!/usr/bin/env python

import rospy
from std_msgs.msg import Int32
from geometry_msgs.msg import PoseStamped,TwistStamped
from styx_msgs.msg import Lane, Waypoint
from trajectory import Trajectory

import math
import tf

'''
This node will publish waypoints from the car's current position to some `x` distance ahead.

As mentioned in the doc, you should ideally first implement a version which does not care
about traffic lights or obstacles.

Once you have created dbw_node, you will update this node to use the status of traffic lights too.

Please note that our simulator also provides the exact location of traffic lights and their
current status in `/vehicle/traffic_lights` message. You can use this message to build this node
as well as to verify your TL classifier.

TODO (for Yousuf and Aaron): Stopline location for each traffic light.
'''

LOOKAHEAD_WPS = 200 # Number of waypoints we will publish. You can change this number
STATE_KEEP_VELOCITY = 0
STATE_SLOWING_DOWN = 1
STATE_WAIT_AT_TL = 2

class WaypointUpdater(object):
    def __init__(self):
        rospy.init_node('waypoint_updater', log_level=rospy.DEBUG)
        rospy.Subscriber('/current_pose', PoseStamped, self.pose_cb)
        rospy.Subscriber('/current_velocity', TwistStamped, self.velocity_cb)
        rospy.Subscriber('/base_waypoints', Lane, self.waypoints_cb)
        rospy.Subscriber('/traffic_waypoint', Int32, self.traffic_cb)
        rospy.Subscriber('/obstacle_waypoint', Waypoint, self.obstacle_cb)

        self.final_waypoints_pub = rospy.Publisher('final_waypoints', Lane, queue_size=1)

        # Member variables for the 2d vehicle pose:
        self.px = None
        self.py = None
        self.yaw = None
        self.velocity = None
        self.waypoints = None
        self.current_waypoint_idx = None
        self.target_velocity = 10.0
        self.state = STATE_KEEP_VELOCITY
        self.red_tl_waypoint_idx = -1
        self.trajectory_start_idx = -1
        self.trajectory = None

        r = rospy.Rate(10)
        while not rospy.is_shutdown():
            self.publish_waypoints()
            r.sleep()


    def pose_cb(self, msg):
        # Get the position in world coordinates:
        self.px = msg.pose.position.x
        self.py = msg.pose.position.y

        # Calculate yaw in world coordinates:
        orientation = msg.pose.orientation
        q = [orientation.x, orientation.y, orientation.z, orientation.w]
        _,_,self.yaw = tf.transformations.euler_from_quaternion(q)
        rospy.logdebug("px = %.2f, py = %.2f, yaw = %.2f", self.px, self.py, self.yaw)


    def velocity_cb(self, msg):
        self.velocity = msg.twist.linear.x


    def distance_to_waypoint(self, wp):
        wx, wy = wp.pose.pose.position.x, wp.pose.pose.position.y
        dx = wx - self.px
        dy = wy - self.py
        return math.sqrt(dx*dx + dy*dy)


    def transform_to_local(self, wp):
        """
        Transforms a waypoint into the vehicle coordinate system.
        :param wp: Waypoint to transform.
        :returns: X, Y and bearing of the waypoint in vehicle coordinates.
        """
        wx, wy = wp.pose.pose.position.x, wp.pose.pose.position.y
        dx = wx - self.px
        dy = wy - self.py
        local_wx = math.cos(-self.yaw) * dx - math.sin(-self.yaw) * dy
        local_wy = math.sin(-self.yaw) * dx + math.cos(-self.yaw) * dy
        return local_wx, local_wy, math.atan2(local_wy, local_wx)


    def is_waypoint_ahead(self, wp):
        wx, wy, phi = self.transform_to_local(wp)
        return wx > 0.0


    def find_closest_waypoint(self):
        """
        Find the closest waypoint to the current vehicle position.
        :param waypoints_msg: Waypoints message containing global waypoints.
        :returns: Index to the closest waypoint in front of vehicle.
        """
        min_dist = 1e9
        min_idx = None

        for idx,wp in enumerate(self.waypoints):
            dist = self.distance_to_waypoint(wp)
            if dist < min_dist:
                min_dist = dist
                min_idx = idx

        # Ensure that the closest waypoint is in front of the car:
        num_wp = len(self.waypoints)
        closest_idx = min_idx
        closest_wp = self.waypoints[closest_idx]
        if not self.is_waypoint_ahead(closest_wp):
            closest_idx = (closest_idx + 1) % num_wp

        return closest_idx


    def calc_waypoint_velocity(self, idx):
        if self.red_tl_waypoint_idx == -1:
            return self.target_velocity
        else:
            return 0.0


    def waypoints_cb(self, waypoints_msg):
        self.waypoints = waypoints_msg.waypoints


    def publish_waypoints(self):
        if self.px is None or self.waypoints is None:
            return

        current_idx = self.find_closest_waypoint()
        self.update_trajectory(current_idx)
        lane = self.execute_trajectory(current_idx)
        self.final_waypoints_pub.publish(lane)


    def execute_trajectory(self, current_idx):
        lane = Lane()
        num_wp = len(self.waypoints)
        dist = self.distance(self.trajectory_start_idx, current_idx)
        last_idx = current_idx
        #self.distance(self.trajectory_start_idx, current_idx)
        # Generate final waypoints
        for i in range(LOOKAHEAD_WPS):
            wp = self.waypoints[current_idx]
            new_wp = Waypoint()
            new_wp.pose = wp.pose
            dist += self.distance(last_idx, current_idx)
            vel = self.trajectory.velocity_at_position(dist)
            last_idx = current_idx
            new_wp.twist.twist.linear.x = vel
            lane.waypoints.append(new_wp)
            current_idx = (current_idx + 1) % num_wp

        return lane


    def update_trajectory(self, current_idx):
        if self.trajectory is None:
            self.trajectory_start_idx = current_idx - 1
            s0 = [0.0, 0.0, 0.0]
            s1 = [0.0, self.target_velocity, 0.0]
            duration = 4.0
            self.trajectory = Trajectory.VelocityKeepingTrajectory(s0, s1, duration)


    def traffic_cb(self, msg):
        self.red_tl_waypoint_idx = msg.data


    def obstacle_cb(self, msg):
        # TODO: Callback for /obstacle_waypoint message. We will implement it later
        pass


    def get_waypoint_velocity(self, waypoint):
        return waypoint.twist.twist.linear.x


    def set_waypoint_velocity(self, waypoints, waypoint, velocity):
        waypoints[waypoint].twist.twist.linear.x = velocity


    def distance(self, wp1, wp2):
        dist = 0
        dl = lambda a, b: math.sqrt((a.x-b.x)**2 + (a.y-b.y)**2  + (a.z-b.z)**2)
        for i in range(wp1, wp2+1):
            dist += dl(self.waypoints[wp1].pose.pose.position, self.waypoints[i].pose.pose.position)
            wp1 = i
        return dist


if __name__ == '__main__':
    try:
        WaypointUpdater()
    except rospy.ROSInterruptException:
        rospy.logerr('Could not start waypoint updater node.')

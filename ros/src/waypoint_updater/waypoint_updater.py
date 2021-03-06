#!/usr/bin/env python

import rospy
from std_msgs.msg import Int32, Float64
from geometry_msgs.msg import PoseStamped,TwistStamped
from styx_msgs.msg import Lane, Waypoint
import numpy as np
from trajectory import Trajectory
from trajectory_generator import TrajectoryGenerator
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

LOOKAHEAD_WPS = 50 # Number of waypoints we will publish. You can change this number

class STATE:
    KEEP_VELOCITY = 0
    STOP_AT_TL = 1


class BreakingTrajectory(object):
    def __init__(self, start_velocity, distance_to_tl, alpha=1.25):
        self.alpha = alpha
        self.acceleration = -0.5 * start_velocity**2/max(0.1, distance_to_tl) * self.alpha
        self.start_velocity = start_velocity
        self.distance_to_tl = distance_to_tl


    def velocity_at_position(self, x):
        if x >= self.distance_to_tl:
            return 0.0

        return math.sqrt(max(0.0, x * self.acceleration + self.start_velocity**2))


class WaypointUpdater(object):
    def __init__(self):
        rospy.init_node('waypoint_updater', log_level=rospy.INFO)
        rospy.Subscriber('/current_pose', PoseStamped, self.pose_cb)
        rospy.Subscriber('/current_velocity', TwistStamped, self.velocity_cb)
        rospy.Subscriber('/base_waypoints', Lane, self.waypoints_cb)
        rospy.Subscriber('/traffic_waypoint', Int32, self.traffic_cb)
        rospy.Subscriber('/obstacle_waypoint', Waypoint, self.obstacle_cb)
        rospy.Subscriber('/target_velocity', Float64, self.target_velocity_cb)

        self.final_waypoints_pub = rospy.Publisher('final_waypoints', Lane, queue_size=1)
        self.cte_pub = rospy.Publisher('current_cte', Float64, queue_size=10)


        # Member variables for the 2d vehicle pose:
        self.px = None
        self.py = None
        self.yaw = None
        self.velocity = None
        self.waypoints = None
        self.current_waypoint_idx = None
        self.target_velocity = rospy.get_param('~velocity', 40.0) / 3.6
        self.state = STATE.KEEP_VELOCITY
        self.red_tl_waypoint_idx = -1
        self.trajectory_start_idx = -1
        self.trajectory_start_velocity = 0.0
        self.trajectory = None
        self.current_velocity = 0.0
        self.current_acceleration = 0.0
        self.velocity_keeping_duration = 4.0
        self.breaking_trajectory_duration = 4.0

        r = rospy.Rate(10)

        while self.px is None or self.waypoints is None:
            r.sleep()

        while not rospy.is_shutdown():
            current_idx = self.find_closest_waypoint()
            self.publish_waypoints(current_idx)
            self.publish_cte(current_idx)
            r.sleep()


    def pose_cb(self, msg):
        # Get the position in world coordinates:
        self.px = msg.pose.position.x
        self.py = msg.pose.position.y

        # Calculate yaw in world coordinates:
        orientation = msg.pose.orientation
        q = [orientation.x, orientation.y, orientation.z, orientation.w]
        _,_,self.yaw = tf.transformations.euler_from_quaternion(q)


    def velocity_cb(self, msg):
        self.velocity = msg.twist.linear.x


    def target_velocity_cb(self, msg):
        self.target_velocity = msg.data


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
        if self.state == STATE.KEEP_VELOCITY:
            return self.target_velocity

        elif idx >= self.red_tl_waypoint_idx:
            return 0.0

        dist = self.distance(self.trajectory_start_idx, idx)
        return self.trajectory.velocity_at_position(dist)



    def waypoints_cb(self, waypoints_msg):
        self.waypoints = waypoints_msg.waypoints


    def get_waypoint_coords(self, current_wp_idx):
        wp_coords = []
        num_wp = len(self.waypoints)
        for i in range(LOOKAHEAD_WPS):
            wp = self.waypoints[current_wp_idx]
            wp_coords.append([wp.pose.pose.position.x, wp.pose.pose.position.y, 1.0])
            current_wp_idx = (current_wp_idx + 1) % num_wp

        return np.array(wp_coords)


    def transformation_matrix(self):
        phi = self.yaw
        R = [[np.cos(-phi), -np.sin(-phi), 0.0],
             [np.sin(-phi),  np.cos(-phi), 0.0],
             [         0.0,           0.0, 1.0]]

        T = [[1.0, 0.0, -self.px],
             [0.0, 1.0, -self.py],
             [0.0, 0.0,      1.0]]

        return np.dot(R,T)


    def transform_waypoints_to_local(self, wp_coords_global):
        m = self.transformation_matrix()
        return np.dot(wp_coords_global,m.transpose())


    def fit_polynomial(self, wp_coords_local):
        return np.polynomial.polynomial.polyfit(wp_coords_local[:,0], wp_coords_local[:,1], 3)


    def publish_cte(self, current_wp_idx):
        wp_coords_global = self.get_waypoint_coords(current_wp_idx)
        wp_coords_local = self.transform_waypoints_to_local(wp_coords_global)
        polynomial = self.fit_polynomial(wp_coords_local)
        self.cte_pub.publish(polynomial[0])


    def publish_waypoints(self, current_idx):
        ego_idx = current_idx
        self.update_state(current_idx)

        lane = Lane()
        num_wp = len(self.waypoints)

        for i in range(LOOKAHEAD_WPS):
            wp = self.waypoints[current_idx]
            new_wp = Waypoint()
            new_wp.pose = wp.pose
            new_wp.twist.twist.linear.x = self.calc_waypoint_velocity(current_idx)
            lane.waypoints.append(new_wp)
            #print(lane.waypoints)
            current_idx = (current_idx + 1) % num_wp

        self.final_waypoints_pub.publish(lane)


    def is_red_traffic_light_near(self, current_idx):
        if self.red_tl_waypoint_idx == -1:
            return False
        
        dist_to_tl = self.distance(current_idx, self.red_tl_waypoint_idx)

        if current_idx > self.red_tl_waypoint_idx:
            return dist_to_tl < 2.0

        if self.velocity < 0.1:
            return dist_to_tl < 1.0
        else:
            time_to_tl = dist_to_tl / self.velocity
            return time_to_tl < self.breaking_trajectory_duration 


    def distance_to_next_traffic_light(self, current_idx):
        assert self.red_tl_waypoint_idx != -1
        return self.distance(current_idx, self.red_tl_waypoint_idx)

    
    def switch_to_velocity_keeping(self, start_state):
        end_state = [0.0, self.target_velocity, 0.0]

        self.trajectory = Trajectory.VelocityKeepingTrajectory(
            start_state, end_state, self.velocity_keeping_duration, 0.0)

        self.state = STATE.KEEP_VELOCITY


    def update_state(self, current_idx):
        tl_near = self.is_red_traffic_light_near(current_idx)

        if self.state == STATE.KEEP_VELOCITY and tl_near:
            self.state = STATE.STOP_AT_TL
            self.trajectory_start_idx = current_idx
            self.trajectory = BreakingTrajectory(
                self.current_velocity,
                self.distance_to_next_traffic_light(current_idx))


        elif self.state == STATE.STOP_AT_TL and not tl_near:
            self.state = STATE.KEEP_VELOCITY
            self.trajectory = None


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

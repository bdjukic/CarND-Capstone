from math import atan

class YawController(object):
    def __init__(self, wheel_base, steer_ratio, min_speed, max_lat_accel, max_steer_angle):
        self.wheel_base = wheel_base
        self.steer_ratio = steer_ratio
        self.min_speed = min_speed
        self.max_lat_accel = max_lat_accel
        self.max_steer_angle = max_steer_angle


    @staticmethod
    def clamp(value, max_abs_value):
        return max(-max_abs_value, min(max_abs_value, value))


    def get_angle(self, radius):
        angle = atan(self.wheel_base / radius) * self.steer_ratio
        return self.clamp(angle, self.max_steer_angle)


    def get_steering(self, linear_velocity, angular_velocity, current_velocity):
        if abs(linear_velocity) > 0.0:
            angular_velocity = current_velocity * angular_velocity / linear_velocity
        else:
            return 0.0

        if abs(current_velocity) > 0.1:
            max_yaw_rate = abs(self.max_lat_accel / current_velocity)
            angular_velocity = self.clamp(angular_velocity, max_yaw_rate)
        else:
            return 0.0

        if abs(angular_velocity) > 0:
            radius = max(current_velocity, self.min_speed) / angular_velocity
            return self.get_angle(radius)
        else:
            return 0.0

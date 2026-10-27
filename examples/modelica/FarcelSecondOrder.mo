model FarcelSecondOrder
  parameter Real T(min=1e-9) = 0.5;
  input Real u;
  output Real y(start=0, fixed=true);
protected
  Real x(start=0, fixed=true);
equation
  T * der(x) = u - x;
  T * der(y) = x - y;
end FarcelSecondOrder;
